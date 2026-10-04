import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/core/websocket/ws_client.dart';
import 'package:omniscribe_client/core/websocket/ws_frames.dart';

void main() {
  group('WsClient & WsEnvelope Frame Parsing', () {
    test('PongFrame parses from type: pong json', () {
      final json = <String, dynamic>{'type': 'pong'};
      final envelope = WsEnvelope.fromJson(json);
      expect(envelope, isA<PongFrame>());
      expect(envelope.toJson(), {'type': 'pong'});
    });

    test('ConnectedFrame parses from type: connected json', () {
      final json = <String, dynamic>{
        'type': 'connected',
        'channel_id': 'chan-123'
      };
      final envelope = WsEnvelope.fromJson(json);
      expect(envelope, isA<ConnectedFrame>());
      expect((envelope as ConnectedFrame).channelId, 'chan-123');
    });

    test('ProgressFrame parses from standard progress json', () {
      final json = <String, dynamic>{
        'type': 'progress',
        'status': 'Processing page 1',
        'percent': 50,
        'stage': 'ocr',
      };
      final envelope = WsEnvelope.fromJson(json);
      expect(envelope, isA<ProgressFrame>());
      final frame = envelope as ProgressFrame;
      expect(frame.status, 'Processing page 1');
      expect(frame.percent, 50);
      expect(frame.stage, 'ocr');
    });

    test('UnknownFrame returned for unknown message type', () {
      final json = <String, dynamic>{'type': 'custom_event', 'foo': 'bar'};
      final envelope = WsEnvelope.fromJson(json);
      expect(envelope, isA<UnknownFrame>());
      expect((envelope as UnknownFrame).type, 'custom_event');
    });

    test('WsClient starts in disconnected state', () {
      final client = WsClient();
      expect(client.state, WsConnectionState.disconnected);
      expect(client.isConnected, isFalse);
      client.dispose();
    });
  });
}
