import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

/// In-process stub of the OmniScribe FastAPI server for integration tests.
///
/// Serves the exact endpoints the workstation golden path touches: the
/// progress-session open, the WebSocket progress channel, the synchronous
/// OCR processing route, page-preview rasterization, and a JSON catch-all
/// for boot-time config fetches. Unmatched POSTs return `{"cancelled": true}`
/// so channel-cancel teardown calls succeed without individual handlers.
class StubOmniscribeServer {
  HttpServer? _server;
  WebSocket? _channel;

  /// Frames pushed over the WebSocket so tests can assert delivery.
  final List<Map<String, Object?>> sentFrames = [];

  String get baseUrl => 'http://127.0.0.1:${_server!.port}';

  static const String pdfBytesText = '''%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj
trailer<</Root 1 0 R>>
%%EOF
''';

  static final Uint8List pdfBytes = Uint8List.fromList(
    utf8.encode(pdfBytesText),
  );

  static final Uint8List pngBytes = base64Decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  );

  Future<void> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server!.listen(_handleRequest);
  }

  Future<void> stop() async {
    await _channel?.close();
    await _server?.close(force: true);
    _channel = null;
    _server = null;
  }

  Future<void> _handleRequest(HttpRequest request) async {
    final path = request.uri.path;

    if (WebSocketTransformer.isUpgradeRequest(request) &&
        path.startsWith('/ws/')) {
      _channel = await WebSocketTransformer.upgrade(request);
      return;
    }

    switch (path) {
      case '/api/progress/session':
        await _drain(request);
        await _respondJson(request, <String, Object?>{
          'channel_id': 'ch-integration',
          'session_token': 'sess-integration',
        });

      case '/api/process':
        await _drain(request);
        // Deliver a streaming frame mid-processing, like the real daemon.
        await _pushFrame(<String, Object?>{
          'type': 'block_complete',
          'page_idx': 0,
          'block_idx': 0,
          'bbox': [0.1, 0.1, 0.9, 0.3],
          'text': 'Integrated block text',
          'kind': 'paragraph',
          'confidence': 0.97,
        });
        await Future<void>.delayed(const Duration(milliseconds: 50));
        final response = request.response;
        response.headers.set('x-text-artifact-id', 'art-integration');
        response.headers.set('x-text-artifact-token', 'tok-integration');
        response.headers.set('x-document-id', 'doc-integration');
        response.headers.set('x-total-pages', '1');
        response.headers.set('x-page-width', '200');
        response.headers.set('x-page-height', '200');
        response.headers.contentType = ContentType.binary;
        response.add(pdfBytes);
        await response.close();

      case '/api/documents/preview':
        await _drain(request);
        final response = request.response;
        response.headers.set('x-total-pages', '1');
        response.headers.set('x-page-width', '200');
        response.headers.set('x-page-height', '200');
        response.headers.set('x-document-id', 'doc-integration');
        response.headers.contentType = ContentType.binary;
        response.add(pngBytes);
        await response.close();

      default:
        await _drain(request);
        await _respondJson(request, <String, Object?>{
          if (request.method == 'POST') 'cancelled': true,
        });
    }
  }

  Future<void> _pushFrame(Map<String, Object?> frame) async {
    final channel = _channel;
    if (channel == null) return;
    sentFrames.add(frame);
    channel.add(jsonEncode(frame));
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }

  Future<void> _drain(HttpRequest request) async {
    try {
      await request.drain<void>().timeout(const Duration(seconds: 10));
    } catch (_) {
      // Body streaming issues must not break the response path.
    }
  }

  Future<void> _respondJson(HttpRequest request, Object? body) async {
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(body));
    await request.response.close();
  }
}
