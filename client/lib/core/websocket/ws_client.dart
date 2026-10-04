import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/websocket/ws_frames.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

/// Connection states for the OmniScribe WebSocket client.
enum WsConnectionState {
  disconnected,
  connecting,
  connected,
  reconnecting,
  error,
  closed,
}

/// Robust WebSocket client for OmniScribe progress streaming with
/// token authentication handshake, line-delimited frame decoding,
/// and auto-reconnect with exponential backoff.
class WsClient {
  WsClient({
    String defaultWsBaseUrl = ApiConstants.defaultWsUrl,
    int maxReconnectAttempts = 5,
    Duration initialBackoff = const Duration(milliseconds: 500),
    Duration maxBackoff = const Duration(seconds: 10),
  })  : _wsBaseUrl = defaultWsBaseUrl,
        _maxReconnectAttempts = maxReconnectAttempts,
        _initialBackoff = initialBackoff,
        _maxBackoff = maxBackoff;

  String _wsBaseUrl;
  final int _maxReconnectAttempts;
  final Duration _initialBackoff;
  final Duration _maxBackoff;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _channelSub;

  final StreamController<WsEnvelope> _envelopeController =
      StreamController<WsEnvelope>.broadcast();
  final StreamController<WsConnectionState> _stateController =
      StreamController<WsConnectionState>.broadcast();
  final StreamController<void> _closedController =
      StreamController<void>.broadcast();

  WsConnectionState _state = WsConnectionState.disconnected;
  String? _currentChannelId;
  String? _currentSessionToken;
  bool _manualDisconnect = false;
  int _reconnectAttempts = 0;
  Timer? _reconnectTimer;

  Stream<WsEnvelope> get stream => _envelopeController.stream;
  Stream<WsConnectionState> get stateStream => _stateController.stream;

  /// Emits when the active server connection ends without a local disconnect.
  Stream<void> get closedStream => _closedController.stream;
  WsConnectionState get state => _state;
  bool get isConnected => _state == WsConnectionState.connected;

  set wsBaseUrl(String url) {
    _wsBaseUrl = url;
  }

  void _setState(WsConnectionState newState) {
    _state = newState;
    if (!_stateController.isClosed) {
      _stateController.add(newState);
    }
  }

  /// Connect to a specific progress channel with session token handshake.
  Future<void> connect({
    required String channelId,
    required String sessionToken,
    String? wsUrl,
  }) async {
    _manualDisconnect = false;
    _reconnectAttempts = 0;
    _currentChannelId = channelId;
    _currentSessionToken = sessionToken;

    final baseUrl = wsUrl ?? _wsBaseUrl;
    final normalizedBase = baseUrl.endsWith('/')
        ? baseUrl.substring(0, baseUrl.length - 1)
        : baseUrl;
    final endpoint = '$normalizedBase${ApiConstants.wsProgress(channelId)}';

    await _performConnect(Uri.parse(endpoint), sessionToken);
  }

  Future<void> _performConnect(Uri uri, String sessionToken) async {
    _stopKeepAlive();
    _cleanupChannel();
    _setState(
      _reconnectAttempts > 0
          ? WsConnectionState.reconnecting
          : WsConnectionState.connecting,
    );

    try {
      final channel = WebSocketChannel.connect(uri);
      await channel.ready;
      _channel = channel;
      _setState(WsConnectionState.connected);
      _reconnectAttempts = 0;

      // 1. Send the required Auth Frame as first frame per backend contract
      final authFrame = jsonEncode({
        'type': 'auth',
        'session_token': sessionToken,
      });
      channel.sink.add(authFrame);

      // 2. Listen for incoming line-delimited JSON frames
      _channelSub = channel.stream.listen(
        _handleMessage,
        onError: _onError,
        onDone: _onDone,
        cancelOnError: false,
      );

      // 3. Sprint 3 / H-4 audit fix: start an application-level
      // keep-alive. WebSocket's TCP layer surfaces half-open
      // connections only when the OS gives up on the keep-alive
      // (often 2 hours). A 20-second application ping + 5-second
      // pong timeout means dead sockets are detected in <30 s.
      _startKeepAlive();
    } catch (e) {
      _onError(e);
    }
  }

  /// Sprint 3 / H-4 audit fix: keep the WS connection alive behind
  /// NAT / proxy idle timeouts. Emits a JSON ``{"type": "ping"}``
  /// every 20 s; if no frame arrives within 5 s the connection is
  /// considered half-open and we tear it down so the auto-reconnect
  /// path takes over.
  Timer? _keepAliveTimer;
  Timer? _pongWatchdog;
  final int _keepAliveIntervalMs = 20000;
  final int _keepAliveTimeoutMs = 5000;

  void _startKeepAlive() {
    _keepAliveTimer?.cancel();
    _pongWatchdog?.cancel();
    _keepAliveTimer = Timer.periodic(
      Duration(milliseconds: _keepAliveIntervalMs),
      (_) => _sendKeepAlivePing(),
    );
  }

  void _sendKeepAlivePing() {
    if (_channel == null || _state != WsConnectionState.connected) {
      return;
    }
    try {
      _channel!.sink.add(jsonEncode(<String, dynamic>{'type': 'ping'}));
    } catch (_) {
      // sink.add can fail if the socket just closed; let _onError
      // drive the reconnect path.
      return;
    }
    _pongWatchdog?.cancel();
    _pongWatchdog = Timer(
      Duration(milliseconds: _keepAliveTimeoutMs),
      () {
        if (_state == WsConnectionState.connected) {
          // No pong / frame within the watchdog window — the
          // socket is half-open. Force a reconnect.
          _onError('keep-alive timeout');
        }
      },
    );
  }

  void _stopKeepAlive() {
    _keepAliveTimer?.cancel();
    _keepAliveTimer = null;
    _pongWatchdog?.cancel();
    _pongWatchdog = null;
  }

  void _handleMessage(dynamic rawMessage) {
    _pongWatchdog?.cancel();
    _pongWatchdog = null;

    if (rawMessage is! String) {
      if (rawMessage is List<int>) {
        final decoded = utf8.decode(rawMessage);
        _parseLineDelimitedJson(decoded);
      }
      return;
    }
    _parseLineDelimitedJson(rawMessage);
  }

  void _parseLineDelimitedJson(String payload) {
    final lines = payload.split('\n');
    for (final line in lines) {
      final trimmed = line.trim();
      if (trimmed.isEmpty) continue;
      _pongWatchdog?.cancel();
      _pongWatchdog = null;
      try {
        final dynamic decoded = jsonDecode(trimmed);
        if (decoded is Map<String, dynamic>) {
          if (decoded['type'] == 'pong') {
            // Heartbeat pong received — watchdog reset
            _pongWatchdog?.cancel();
            _pongWatchdog = null;
            continue;
          }
          final envelope = WsEnvelope.fromJson(decoded);
          if (!_envelopeController.isClosed) {
            _envelopeController.add(envelope);
          }
        }
      } catch (e) {
        // Fallback for non-standard or malformed frame
        if (!_envelopeController.isClosed) {
          _envelopeController.add(
            UnknownFrame(
              type: 'parse_error',
              rawData: <String, dynamic>{'raw': trimmed, 'error': e.toString()},
            ),
          );
        }
      }
    }
  }

  void _onError(dynamic error) {
    _setState(WsConnectionState.error);
    _scheduleReconnect();
  }

  void _onDone() {
    _stopKeepAlive();
    if (!_manualDisconnect) {
      _setState(WsConnectionState.disconnected);
      if (!_closedController.isClosed) {
        _closedController.add(null);
      }
      _scheduleReconnect();
    } else {
      _setState(WsConnectionState.closed);
    }
  }

  void _scheduleReconnect() {
    if (_manualDisconnect) return;
    if (_currentChannelId == null || _currentSessionToken == null) return;
    if (_reconnectAttempts >= _maxReconnectAttempts) {
      _setState(WsConnectionState.closed);
      return;
    }

    _reconnectAttempts++;
    final delayMs =
        (_initialBackoff.inMilliseconds * math.pow(1.5, _reconnectAttempts - 1))
            .toInt();
    final clampedDelay = Duration(
      milliseconds: math.min(delayMs, _maxBackoff.inMilliseconds),
    );

    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(clampedDelay, () {
      if (_manualDisconnect) return;
      if (_currentChannelId != null && _currentSessionToken != null) {
        final normalizedBase = _wsBaseUrl.endsWith('/')
            ? _wsBaseUrl.substring(0, _wsBaseUrl.length - 1)
            : _wsBaseUrl;
        final endpoint =
            '$normalizedBase${ApiConstants.wsProgress(_currentChannelId!)}';
        _performConnect(Uri.parse(endpoint), _currentSessionToken!);
      }
    });
  }

  /// Send a JSON message over the active socket.
  void send(Map<String, dynamic> message) {
    if (_channel != null && _state == WsConnectionState.connected) {
      _channel!.sink.add(jsonEncode(message));
    }
  }

  /// Signal cooperative job/channel cancellation to server via WebSocket frame.
  void cancelChannel() {
    send(<String, dynamic>{'type': 'cancel'});
  }

  /// Disconnect the active channel.
  Future<void> disconnect() async {
    _manualDisconnect = true;
    _reconnectTimer?.cancel();
    _cleanupChannel();
    _setState(WsConnectionState.disconnected);
  }

  void _cleanupChannel() {
    _channelSub?.cancel();
    _channelSub = null;
    _channel?.sink.close();
    _channel = null;
  }

  /// Dispose all streams and controllers.
  void dispose() {
    _manualDisconnect = true;
    _reconnectTimer?.cancel();
    _stopKeepAlive();
    _cleanupChannel();
    _envelopeController.close();
    _stateController.close();
    _closedController.close();
  }
}
