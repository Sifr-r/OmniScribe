import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:omniscribe_client/features/jobs/job_orchestration_notifier.dart';
import 'package:omniscribe_client/features/workstation/process_settings.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/main.dart';

/// Q12 real-server variant: boots the app against a **live**
/// `omniscribe-server` process (spawned via `uv run`) and drives the
/// server-touching golden path that needs no VLM: health probe →
/// sample-PDF fetch (`/api/sample-pdf/digital.pdf`, the auth-exempt
/// fixture route) → document staging → real server-side preview
/// rasterization (`/api/documents/preview`).
///
/// Skips (rather than fails) on machines without the Python tooling so
/// constrained environments still run the stub-server variant
/// (`app_workstation_test.dart`).
/// With `--dart-define=OMNISCRIBE_LIVE_OCR=true`, also run model-backed OCR
/// on the raster preview (no embedded text), using the server's runtime config.
void main() {
  const liveOcr = bool.fromEnvironment('OMNISCRIBE_LIVE_OCR');
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;

  testWidgets('workstation connects to a live omniscribe-server', (
    tester,
  ) async {
    final server = await _LiveServer.spawn(requiredForOcr: liveOcr);
    if (server == null) {
      markTestSkipped(
        'uv / omniscribe-server tooling unavailable on this machine; '
        'the real-server integration variant requires a local Python env.',
      );
      return;
    }
    addTearDown(server.stop);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          apiBaseUrlProvider.overrideWith(
            () => _FixedBaseUrl('http://127.0.0.1:${server.port}'),
          ),
        ],
        child: const OmniScribeApp(),
      ),
    );
    await tester.pumpAndSettle(const Duration(seconds: 2));

    final container = ProviderScope.containerOf(
      tester.element(find.byType(OmniScribeApp)),
    );
    // Same session-only bearer wiring as Settings; the child inherits this env.
    container.read(authTokenProvider.notifier).set(
          Platform.environment['OMNISCRIBE_AUTH_TOKEN']?.trim(),
        );
    final notifier = container.read(workstationProvider.notifier);

    await notifier.tryWithSamplePdf(name: 'digital.pdf');

    await _waitFor(
      tester,
      () {
        final state = container.read(workstationProvider);
        final jobStage = container.read(jobOrchestrationProvider).stage;
        return state.hasDocument && jobStage != 'Downloading';
      },
      timeout: const Duration(seconds: 60),
      context: 'sample PDF fetch + staging',
    );

    final state = container.read(workstationProvider);
    expect(state.hasDocument, isTrue);
    expect(state.filename, 'digital.pdf');

    // The real server rasterized a preview for page 0 (PyMuPDF round-trip);
    // pageCount grows from 0 when the preview's x-total-pages header lands.
    await _waitFor(
      tester,
      () =>
          container.read(workstationProvider).pages.isNotEmpty &&
          container.read(workstationProvider).pages.first.previewBytes != null,
      timeout: const Duration(seconds: 60),
      context: 'server-side preview rasterization',
    );

    final previewState = container.read(workstationProvider);
    expect(previewState.pageCount, greaterThanOrEqualTo(1));
    final preview = previewState.pages.first.previewBytes;
    expect(preview, isNotNull);
    expect(preview!.lengthInBytes, greaterThan(0));
    expect(previewState.previewError, isNull);

    if (liveOcr) {
      final config = await container
          .read(configRepositoryProvider)
          .getConfig()
          .timeout(const Duration(seconds: 30));
      final endpoint = Uri.tryParse(config.apiBase.trim());
      if (endpoint == null ||
          !const {'http', 'https'}.contains(endpoint.scheme) ||
          endpoint.host.isEmpty ||
          endpoint.userInfo.isNotEmpty ||
          config.model.trim().isEmpty) {
        fail('Live OCR requires a valid LLM_API_BASE and non-empty LLM_MODEL '
            'in the spawned server runtime configuration.');
      }
      // Raster bytes carry no PDF text layer, so this cannot pass by extracting
      // the digital fixture's existing text. Hybrid OCR must invoke the VLM.
      notifier.loadDocument(preview, 'digital-page-0.png');
      final orchestration = container.read(jobOrchestrationProvider.notifier);
      const ocrTimeout = Duration(minutes: 5);
      try {
        await orchestration
            .processOcrSync(
              settings: ProcessSettings(
                apiBase: config.apiBase,
                model: config.model,
                // Omit the masked config API key; the backend resolves its key.
                qualityLoopEnabled: false,
              ),
              receiveTimeout: ocrTimeout,
            )
            .timeout(ocrTimeout);
      } catch (error) {
        final job = container.read(jobOrchestrationProvider);
        fail('Live OCR failed (${error.runtimeType}); stage=${job.stage}, '
            'processedBlocks=${job.processedBlocks}. Check server/VLM logs, '
            'LLM_API_BASE, LLM_MODEL, LLM_API_KEY and backend authentication.');
      }
      final job = container.read(jobOrchestrationProvider);
      expect(job.isProcessing, isFalse);
      expect(job.stage, 'Complete', reason: job.statusMessage);
      expect(job.error, isNull);
      final processed = container.read(workstationProvider);
      final text = processed.allBBoxes
          .map((block) => block.text)
          .join(' ')
          .replaceAll(RegExp(r'\s+'), ' ')
          .toLowerCase();
      expect(text, contains('computer science'),
          reason:
              'OCR must recognize known content from the rasterized fixture');
      final pdf = processed.processedPdfBytes;
      expect(pdf, isNotNull, reason: 'OCR must adopt the returned PDF');
      expect(pdf!.length, greaterThan(5));
      expect(ascii.decode(pdf.take(5).toList()), '%PDF-');
      final outputPreview = await container
          .read(ocrRepositoryProvider)
          .renderDocumentPagePreview(
            fileBytes: pdf,
            filename: 'processed.pdf',
          )
          .timeout(const Duration(seconds: 30));
      expect(outputPreview, isNotNull,
          reason: 'Returned PDF must be readable by the real preview renderer');
      expect(outputPreview!.bytes, isNotEmpty);
    }
  }, timeout: const Timeout(Duration(minutes: 10)));
}

/// Polls a real-async condition while keeping frames flowing; network
/// round-trips against a spawned process have unpredictable latency, so
/// fixed `pumpAndSettle` durations are not sufficient.
Future<void> _waitFor(
  WidgetTester tester,
  bool Function() condition, {
  required Duration timeout,
  required String context,
}) async {
  final deadline = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(deadline)) {
    if (condition()) return;
    await tester.pump(const Duration(milliseconds: 100));
    await Future<void>.delayed(const Duration(milliseconds: 50));
  }
  fail('Timed out after $timeout waiting for: $context');
}

/// Spawns the real FastAPI server as a child process and waits for
/// `/api/health`. Returns null when the Python tooling is unavailable.
class _LiveServer {
  _LiveServer._(
      this.port, this._process, this._output, this._runtimeDirectory) {
    unawaited(_process.exitCode.then<void>((_) {
      _hasExited = true;
    }));
  }

  final int port;
  final Process _process;
  final StringBuffer _output;
  final Directory _runtimeDirectory;
  Future<void>? _stopping;
  bool _hasExited = false;

  static Future<_LiveServer?> spawn({bool requiredForOcr = false}) async {
    final repoRoot = _findRepoRoot();
    if (repoRoot == null) {
      if (requiredForOcr) {
        throw StateError('Live OCR requires the OmniScribe repository root '
            '(pyproject.toml) above the integration test working directory.');
      }
      return null;
    }

    final port = await _freePort();
    final temporaryRoot = Directory(
      '${repoRoot.path}${Platform.pathSeparator}.agent-tmp',
    );
    await temporaryRoot.create(recursive: true);
    if (!temporaryRoot.resolveSymbolicLinksSync().startsWith(
          '${repoRoot.resolveSymbolicLinksSync()}${Platform.pathSeparator}',
        )) {
      throw StateError('Integration runtime directory must remain in the repo');
    }
    final runtimeDirectory = await temporaryRoot.createTemp('live-server-');
    Process process;
    try {
      process = await Process.start(
        'uv',
        [
          'run',
          'omniscribe-server',
          '--host',
          '127.0.0.1',
          '--port',
          '$port',
        ],
        workingDirectory: repoRoot.path,
        environment: {
          'PYTHONUNBUFFERED': '1',
          'OMNISCRIBE_ARTIFACT_DIR': runtimeDirectory.path,
          'OMNISCRIBE_SPOOL_DIR': runtimeDirectory.path,
          'OMNISCRIBE_STATE_BACKEND': 'memory',
          'OMNISCRIBE_JOBS_MODE': 'inprocess',
          'TMP': runtimeDirectory.path,
          'TEMP': runtimeDirectory.path,
          'TMPDIR': runtimeDirectory.path,
        },
      );
    } on ProcessException catch (error) {
      await runtimeDirectory.delete(recursive: true);
      if (requiredForOcr) {
        throw StateError('Live OCR could not start uv run omniscribe-server: '
            '${error.message}');
      }
      return null;
    }

    final server = _LiveServer._(
      port,
      process,
      StringBuffer(),
      runtimeDirectory,
    );
    process.stdout
        .transform(systemEncoding.decoder)
        .listen(server._output.write);
    process.stderr
        .transform(systemEncoding.decoder)
        .listen(server._output.write);

    final healthy = await server._waitHealthy();
    if (!healthy) {
      await server.stop();
      if (requiredForOcr) {
        final output = server._output.toString();
        throw StateError('Live OCR backend did not become healthy within 120s. '
            'Server output: ${output.length > 4000 ? output.substring(output.length - 4000) : output}');
      }
      return null;
    }
    return server;
  }

  Future<bool> _waitHealthy() async {
    final deadline = DateTime.now().add(const Duration(seconds: 120));
    final client = HttpClient();
    client.connectionTimeout = const Duration(seconds: 3);
    while (DateTime.now().isBefore(deadline)) {
      try {
        final request = await client
            .getUrl(Uri.parse('http://127.0.0.1:$port/api/health'))
            .timeout(const Duration(seconds: 3));
        final response =
            await request.close().timeout(const Duration(seconds: 3));
        final body = await response
            .transform(utf8.decoder)
            .join()
            .timeout(const Duration(seconds: 3));
        if (response.statusCode == 200 && body.contains('"status":"ok"')) {
          client.close(force: true);
          return true;
        }
      } catch (_) {
        // Server not accepting connections yet; keep polling.
      }
      await Future<void>.delayed(const Duration(milliseconds: 500));
    }
    client.close(force: true);
    return false;
  }

  Future<void> stop() => _stopping ??= _stop();

  Future<void> _stop() async {
    if (Platform.isWindows) {
      if (_hasExited) {
        // Once uv exits its PID may be reused; never target that PID again.
        throw StateError('Test-owned server launcher has already exited; '
            'descendant termination cannot be established safely. Retained '
            'runtime files at ${_runtimeDirectory.path}.');
      }
      // uv owns a Python descendant on Windows; killing only uv leaks it.
      final ProcessResult result;
      try {
        result = await Process.run(
          'taskkill',
          ['/PID', '${_process.pid}', '/T', '/F'],
        ).timeout(const Duration(seconds: 10));
      } catch (error) {
        throw StateError('Test-owned server termination failed '
            '(PID ${_process.pid}); retained runtime files at '
            '${_runtimeDirectory.path}: $error');
      }
      if (result.exitCode != 0) {
        throw StateError('Could not terminate the test-owned server process '
            'tree (PID ${_process.pid}, taskkill exit ${result.exitCode}); '
            'retained runtime files at ${_runtimeDirectory.path}. '
            '${result.stderr}');
      }
    } else {
      _process.kill();
    }
    // Keep files if termination is uncertain; do not race child writes.
    await _process.exitCode.timeout(const Duration(seconds: 10));
    await _runtimeDirectory.delete(recursive: true);
  }

  static Directory? _findRepoRoot() {
    Directory? dir = Directory.current;
    while (dir != null) {
      if (File('${dir.path}${Platform.pathSeparator}pyproject.toml')
          .existsSync()) {
        return dir;
      }
      final parent = dir.parent;
      if (parent.path == dir.path) return null;
      dir = parent;
    }
    return null;
  }

  static Future<int> _freePort() async {
    final socket = await ServerSocket.bind('127.0.0.1', 0);
    final port = socket.port;
    await socket.close();
    return port;
  }
}

class _FixedBaseUrl extends ApiBaseUrlNotifier {
  _FixedBaseUrl(this.url);
  final String url;

  @override
  String build() => url;
}
