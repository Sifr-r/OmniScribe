import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:omniscribe_client/features/jobs/job_orchestration_notifier.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/main.dart';

import 'stub_omniscribe_server.dart';

/// Boots the real app against an in-process stubbed OmniScribe server and
/// drives the workstation golden path: server connect (progress session +
/// WebSocket attach) → document load → OCR submit → streamed block frame →
/// rendered result.
void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;

  // A minimal single-page PDF; the stub never parses it.
  final pdfPayload = Uint8List.fromList(
    utf8.encode(StubOmniscribeServer.pdfBytesText),
  );

  testWidgets('workstation golden path against the stub server', (
    tester,
  ) async {
    final stub = StubOmniscribeServer();
    await stub.start();
    addTearDown(stub.stop);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          apiBaseUrlProvider.overrideWith(() => _FixedBaseUrl(stub.baseUrl)),
        ],
        child: const OmniScribeApp(),
      ),
    );
    await tester.pumpAndSettle(const Duration(seconds: 2));

    final container = ProviderScope.containerOf(
      tester.element(find.byType(OmniScribeApp)),
    );
    final notifier = container.read(workstationProvider.notifier);

    // Load a document (client-side staging, like a dropped file).
    notifier.loadDocument(pdfPayload, 'integration.pdf');
    await tester.pumpAndSettle(const Duration(seconds: 3));
    expect(container.read(workstationProvider).hasDocument, isTrue);

    // Submit OCR through the app's own entry point.
    await notifier.processCurrentDocument();
    await tester.pumpAndSettle(const Duration(seconds: 5));

    final state = container.read(workstationProvider);
    final job = container.read(jobOrchestrationProvider);
    expect(state.hasDocument, isTrue);
    expect(job.isProcessing, isFalse);
    expect(job.error, isNull);
    expect(job.stage, 'Complete');
    expect(job.percent, 100);
    expect(job.textArtifactId, 'art-integration');
    expect(job.textArtifactToken, 'tok-integration');

    // The streamed WebSocket frame landed as a real bbox on page 0.
    expect(stub.sentFrames, hasLength(1));
    expect(job.processedBlocks, 1);
    expect(state.currentPageBBoxes, hasLength(1));
    expect(state.currentPageBBoxes.first.text, 'Integrated block text');
    expect(state.currentPageBBoxes.first.confidence, closeTo(0.97, 1e-9));

    // The completion status surfaced in the progress dock UI.
    expect(find.textContaining('Document OCR complete'), findsWidgets);
  });
}

class _FixedBaseUrl extends ApiBaseUrlNotifier {
  _FixedBaseUrl(this.url);
  final String url;

  @override
  String build() => url;
}
