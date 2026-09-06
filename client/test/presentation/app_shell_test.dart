import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/core/theme/app_theme.dart';
import 'package:omniscribe_client/data/models/document_result.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import 'package:omniscribe_client/data/repositories/ocr_repository.dart';
import 'package:omniscribe_client/core/websocket/ws_client.dart';
import 'package:omniscribe_client/presentation/features/extraction_screen.dart';
import 'package:omniscribe_client/presentation/features/glossary_screen.dart';
import 'package:omniscribe_client/presentation/features/transcription_screen.dart';
import 'package:omniscribe_client/presentation/features/translation_screen.dart';
import 'package:omniscribe_client/presentation/jobs/job_history_screen.dart';
import 'package:omniscribe_client/presentation/settings/settings_screen.dart';
import 'package:omniscribe_client/presentation/shell/app_shell.dart';
import 'package:omniscribe_client/presentation/shell/shell_state.dart';
import 'package:omniscribe_client/presentation/workstation/workstation_screen.dart';

class _MockOcrRepository extends Mock implements OcrRepository {}

/// Wave 16 / flutter_riverpod 3.4: ``NotifierProvider.overrideWith`` now
/// requires a ``Notifier Function()`` — a Notifier subclass that overrides
/// ``build()`` to produce the desired initial state — instead of the old
/// ``StateProvider`` ``(ref) => value`` closure pattern.
class _AuthRequiredTrue extends AuthRequiredNotifier {
  @override
  bool build() => true;
}

/// Tests mounting [AppShell] pin the health badge to a settled state: the
/// seeded ``checking`` status drives an infinite pulse animation in
/// [ServerHealthBadge], which would never let pumpAndSettle settle.
class _OfflineHealth extends ServerHealthNotifier {
  @override
  ServerHealthState build() =>
      const ServerHealthState(status: ServerHealth.offline);
}

/// No-op WebSocket client for tests. The real [WsClient] tries to open a
/// socket against the configured base URL, which never resolves in the
/// widget-test harness (no real server). The workstation notifier awaits
/// [WsClient.connect] before calling `processOcrSync`, so a hanging real
/// client would block the OCR pipeline behind the Ctrl+Enter shortcut.
class _FakeWsClient extends WsClient {
  _FakeWsClient() : super(defaultWsBaseUrl: 'ws://test.invalid');

  @override
  Future<void> connect({
    required String channelId,
    required String sessionToken,
    String? wsUrl,
  }) async {
    // No-op: skip the real socket handshake so the workstation notifier can
    // move past the WebSocket attach step and reach processOcrSync.
  }

  @override
  Future<void> disconnect() async {
    // No-op.
  }
}

void main() {
  setUpAll(() {
    registerFallbackValue(const ProcessSettings());
    registerFallbackValue(Uint8List(0));
  });

  Widget buildAppShell() {
    return ProviderScope(
      overrides: [
        serverHealthProvider.overrideWith(_OfflineHealth.new),
      ],
      child: MaterialApp(
        theme: AppTheme.darkTheme,
        home: const Scaffold(
          body: AppShell(),
        ),
      ),
    );
  }

  group('Individual Screens Tests', () {
    testWidgets('WorkstationScreen renders cleanly', (tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.darkTheme,
            home: const Scaffold(body: WorkstationScreen()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('OmniScribe'), findsOneWidget);
    });

    testWidgets('TranslationScreen renders cleanly', (tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.darkTheme,
            home: const Scaffold(body: TranslationScreen()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(TranslationScreen), findsOneWidget);
    });

    testWidgets('TranscriptionScreen renders cleanly', (tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.darkTheme,
            home: const Scaffold(body: TranscriptionScreen()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(TranscriptionScreen), findsOneWidget);
    });

    testWidgets('GlossaryScreen renders cleanly', (tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.darkTheme,
            home: const Scaffold(body: GlossaryScreen()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(GlossaryScreen), findsOneWidget);
    });

    testWidgets('ExtractionScreen renders cleanly', (tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.darkTheme,
            home: const Scaffold(body: ExtractionScreen()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(ExtractionScreen), findsOneWidget);
    });

    testWidgets('JobHistoryScreen renders cleanly', (tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.darkTheme,
            home: const Scaffold(body: JobHistoryScreen()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(JobHistoryScreen), findsOneWidget);
    });

    testWidgets('SettingsScreen renders cleanly', (tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.darkTheme,
            home: const Scaffold(body: SettingsScreen()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(SettingsScreen), findsOneWidget);
    });
  });

  group('AppShell & Tab Navigation Tests', () {
    testWidgets('Renders OmniScribe brand and default Workstation tab',
        (WidgetTester tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(buildAppShell());
      await tester.pumpAndSettle();

      expect(find.text('OmniScribe'), findsWidgets);
      expect(find.text('DOCUVERSE 2.0'), findsOneWidget);
    });

    testWidgets('Switches to Translation tab when Translation is tapped',
        (WidgetTester tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(buildAppShell());
      await tester.pumpAndSettle();

      final tab = find.widgetWithText(InkWell, 'Translation');
      await tester.tap(tab);
      await tester.pumpAndSettle();

      expect(find.text('Neural Translation Engine'), findsOneWidget);
    });

    testWidgets('Switches to Transcription tab when Transcription is tapped',
        (WidgetTester tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(buildAppShell());
      await tester.pumpAndSettle();

      final tab = find.widgetWithText(InkWell, 'Transcription');
      await tester.tap(tab);
      await tester.pumpAndSettle();

      expect(find.text('Voice & Audio Transcription'), findsOneWidget);
    });

    testWidgets('Switches to Glossary tab when Glossary is tapped',
        (WidgetTester tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(buildAppShell());
      await tester.pumpAndSettle();

      final tab = find.widgetWithText(InkWell, 'Glossary');
      await tester.tap(tab);
      await tester.pumpAndSettle();

      expect(find.text('Terminology Glossary'), findsOneWidget);
    });

    testWidgets('Switches to Extraction tab when Extraction is tapped',
        (WidgetTester tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(buildAppShell());
      await tester.pumpAndSettle();

      final tab = find.widgetWithText(InkWell, 'Extraction');
      await tester.tap(tab);
      await tester.pumpAndSettle();

      expect(find.text('Structured Information Extraction'), findsOneWidget);
    });

    testWidgets('Switches to Job History tab when Job History is tapped',
        (WidgetTester tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(buildAppShell());
      await tester.pumpAndSettle();

      final tab = find.widgetWithText(InkWell, 'Job History');
      await tester.tap(tab);
      await tester.pumpAndSettle();

      expect(find.text('Job Execution History'), findsOneWidget);
    });

    testWidgets('Switches to Settings tab when Settings is tapped',
        (WidgetTester tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(buildAppShell());
      await tester.pumpAndSettle();

      final tab = find.widgetWithText(InkWell, 'Settings');
      await tester.tap(tab);
      await tester.pumpAndSettle();

      expect(find.text('Settings & Configuration'), findsOneWidget);
    });

    testWidgets('renders AuthRequiredBanner when flag is true', (tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            authRequiredProvider.overrideWith(_AuthRequiredTrue.new),
            serverHealthProvider.overrideWith(_OfflineHealth.new),
          ],
          child: const MaterialApp(home: AppShell()),
        ),
      );
      await tester.pump();
      expect(find.text('Authentication required'), findsOneWidget);
    });
  });

  group('AppShell Keyboard Shortcut Tests', () {
    testWidgets('Ctrl+O on workstation increments filePickSignal',
        (WidgetTester tester) async {
      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(buildAppShell());
      await tester.pumpAndSettle();

      final container = ProviderScope.containerOf(
        tester.element(find.byType(AppShell)),
      );

      // Default tab is workstation, filePickSignal starts at 0.
      expect(container.read(activeTabProvider), AppTab.workstation);
      expect(container.read(workstationProvider).filePickSignal, 0);

      await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
      await tester.sendKeyDownEvent(LogicalKeyboardKey.keyO);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.keyO);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
      await tester.pump();

      expect(
        container.read(workstationProvider).filePickSignal,
        greaterThan(0),
        reason: 'Ctrl+O on workstation should bump the file pick signal',
      );
    });

    testWidgets('Ctrl+Enter on workstation with loaded doc invokes OCR',
        (WidgetTester tester) async {
      final ocrRepo = _MockOcrRepository();
      when(() => ocrRepo.openProgressSession(
            clientId: any(named: 'clientId'),
          )).thenAnswer(
        (_) async => const ProgressSessionHandle(
          channelId: 'test-channel',
          sessionToken: 'test-token',
        ),
      );
      when(() => ocrRepo.processOcrSync(
            fileBytes: any(named: 'fileBytes'),
            filename: any(named: 'filename'),
            settings: any(named: 'settings'),
            progressChannel: any(named: 'progressChannel'),
            progressToken: any(named: 'progressToken'),
            onSendProgress: any(named: 'onSendProgress'),
          )).thenAnswer(
        (_) async => ProcessOcrResult(
          pdfBytes: Uint8List.fromList([1, 2, 3]),
          headers: const {},
        ),
      );
      when(() => ocrRepo.cancelProgressChannel(any()))
          .thenAnswer((_) async => true);

      tester.view.physicalSize = const Size(1920, 1080);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            ocrRepositoryProvider.overrideWithValue(ocrRepo),
            wsClientProvider.overrideWithValue(_FakeWsClient()),
            serverHealthProvider.overrideWith(_OfflineHealth.new),
          ],
          child: MaterialApp(
            theme: AppTheme.darkTheme,
            home: const Scaffold(body: AppShell()),
          ),
        ),
      );
      await tester.pumpAndSettle();

      final container = ProviderScope.containerOf(
        tester.element(find.byType(AppShell)),
      );

      // Default tab is workstation; seed a document so hasDocument flips true.
      container.read(workstationProvider.notifier).loadDocument(
            Uint8List.fromList([1, 2, 3, 4]),
            'invoice.pdf',
            pageCount: 1,
          );
      await tester.pump();
      expect(container.read(workstationProvider).hasDocument, true);

      await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
      await tester.sendKeyDownEvent(LogicalKeyboardKey.enter);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.enter);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
      // Allow async processOcrSync to run.
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      verify(() => ocrRepo.processOcrSync(
            fileBytes: any(named: 'fileBytes'),
            filename: 'invoice.pdf',
            settings: any(named: 'settings'),
            progressChannel: any(named: 'progressChannel'),
            progressToken: any(named: 'progressToken'),
            onSendProgress: any(named: 'onSendProgress'),
          )).called(1);
    });
  });
}
