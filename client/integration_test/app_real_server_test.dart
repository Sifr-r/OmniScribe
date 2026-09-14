import 'dart:convert';
import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:omniscribe_client/data/providers/job_orchestration_notifier.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
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
void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;

  testWidgets('workstation connects to a live omniscribe-server', (
    tester,
  ) async {
    final server = await _LiveServer.spawn();
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
    final notifier = container.read(workstationProvider.notifier);

    await notifier.tryWithSamplePdf(name: 'digital.pdf');

    await _waitFor(
      tester,
      () {
        final state = container.read(workstationProvider);
        final jobStage =
            container.read(jobOrchestrationProvider).stage;
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
      () => container.read(workstationProvider).pages.isNotEmpty &&
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
  });
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
  _LiveServer._(this.port, this._process, this._output);

  final int port;
  final Process _process;
  final StringBuffer _output;

  static Future<_LiveServer?> spawn() async {
    final repoRoot = _findRepoRoot();
    if (repoRoot == null) return null;

    final port = await _freePort();
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
        environment: {'PYTHONUNBUFFERED': '1'},
      );
    } on ProcessException {
      return null;
    }

    final server = _LiveServer._(port, process, StringBuffer());
    process.stdout
        .transform(systemEncoding.decoder)
        .listen(server._output.write);
    process.stderr
        .transform(systemEncoding.decoder)
        .listen(server._output.write);

    final healthy = await server._waitHealthy();
    if (!healthy) {
      server.stop();
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
            .getUrl(Uri.parse('http://127.0.0.1:$port/api/health'));
        final response = await request.close();
        final body = await response.transform(utf8.decoder).join();
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

  void stop() {
    _process.kill();
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
