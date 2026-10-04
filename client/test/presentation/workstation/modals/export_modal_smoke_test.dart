import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/theme/app_theme.dart';
import 'package:omniscribe_client/data/models/models.dart';
import 'package:omniscribe_client/features/jobs/job_orchestration_notifier.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/data/repositories/repositories.dart';
import 'package:omniscribe_client/shared/widgets/app_button.dart';
import 'package:omniscribe_client/shared/widgets/app_select.dart';
import 'package:omniscribe_client/features/documents/export_modal.dart';

class _MockFeatureRepository extends Mock
    implements
        TranslationRepository,
        TranscriptionRepository,
        GlossaryRepository,
        DocumentRepository {}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late _MockFeatureRepository mockRepo;

  setUpAll(() {
    registerFallbackValue(
      const ExportBlockTreeRequest(
        textArtifactId: 'fallback-id',
        textArtifactToken: 'fallback-token',
      ),
    );
    registerFallbackValue(
      const ExportDocxRequest(text: 'fallback-text'),
    );
  });

  setUp(() {
    mockRepo = _MockFeatureRepository();

    // file_picker uses a MethodChannel under the hood. Stub saveFile to
    // return null (user-cancelled path) so we never pop a real system
    // dialog during the test.
    const channel = MethodChannel('mightytec.com/filesystem');
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      if (call.method == 'saveFile') return null;
      return null;
    });
  });

  Widget buildModal(ProviderContainer container) {
    return UncontrolledProviderScope(
      container: container,
      child: MaterialApp(
        theme: AppTheme.darkTheme,
        home: const Scaffold(body: Center(child: ExportModal())),
      ),
    );
  }

  Future<void> selectFormat(WidgetTester tester, String contains) async {
    await tester.tap(find.byType(AppSelect<ExportFormat>));
    await tester.pumpAndSettle();
    final item = find.textContaining(contains);
    await tester.tap(item.last);
    await tester.pumpAndSettle();
  }

  testWidgets('every non-PDF export format completes without throwing',
      (tester) async {
    when(() => mockRepo.exportDocx(any())).thenAnswer(
      (_) async => Uint8List.fromList([1, 2, 3, 4]),
    );
    when(() => mockRepo.exportDocxTree(any())).thenAnswer(
      (_) async => Uint8List.fromList([1, 2, 3, 4]),
    );

    final container = ProviderContainer(
      overrides: [
        translationRepositoryProvider.overrideWithValue(mockRepo),
        transcriptionRepositoryProvider.overrideWithValue(mockRepo),
        glossaryRepositoryProvider.overrideWithValue(mockRepo),
        documentRepositoryProvider.overrideWithValue(mockRepo),
      ],
    );
    addTearDown(container.dispose);

    container.read(workstationProvider.notifier).loadDocument(
          Uint8List.fromList([1, 2, 3]),
          'scan.pdf',
          pageCount: 2,
        );
    container.read(workstationProvider.notifier).addOrUpdateBBox(
          0,
          const BBoxItem(
            blockId: 'p0_b0',
            page: 0,
            block: 0,
            bbox: [0.0, 0.0, 1.0, 0.1],
            text: 'First page block.',
          ),
        );
    container.read(workstationProvider.notifier).addOrUpdateBBox(
          1,
          const BBoxItem(
            blockId: 'p1_b0',
            page: 1,
            block: 0,
            bbox: [0.0, 0.0, 1.0, 0.1],
            text: 'Second page block.',
          ),
        );
    container.read(jobOrchestrationProvider.notifier).setTextArtifact(
          textArtifactId: 'art-123',
          textArtifactToken: 'tok-abc',
        );

    await tester.pumpWidget(buildModal(container));
    await tester.pumpAndSettle();

    // Recognized text and artifact handles do not make the source PDF ready.
    expect(find.text('NO DATA'), findsOneWidget);
    expect(find.text('READY'), findsNothing);

    final formats = <String>{
      'Word Document',
      'DOCX Tree Layout',
      'Standalone HTML',
      'Block Tree JSON',
      'Markdown',
      'Plain Text',
    };

    for (final label in formats) {
      await selectFormat(tester, label);
      expect(find.text('READY'), findsOneWidget,
          reason: '$label must use its own export readiness');
      final exportButton = find.widgetWithText(AppButton, 'Export Document');
      expect(exportButton, findsOneWidget,
          reason: 'export button missing after selecting $label');
      await tester.tap(exportButton);
      // Allow both the export work and the async file picker to resolve.
      await tester.pumpAndSettle(const Duration(milliseconds: 200));

      final failureFinder = find.textContaining('Export failed:');
      final readyFinder = find.textContaining('ready');
      expect(
        failureFinder,
        findsNothing,
        reason: 'export of $label failed; modal caught an exception',
      );
      expect(
        readyFinder,
        findsWidgets,
        reason: 'expected ready status banner after exporting $label',
      );
    }

    container.read(workstationProvider.notifier).loadDocument(
          Uint8List.fromList([4, 5, 6]),
          'replacement.pdf',
        );
    await tester.pumpAndSettle();

    expect(find.text('replacement.pdf'), findsOneWidget);
    expect(find.text('NO DATA'), findsOneWidget);
    expect(find.text('READY'), findsNothing);

    await selectFormat(tester, 'DOCX Tree Layout');
    expect(find.text('NO DATA'), findsOneWidget,
        reason: 'replacement documents must not reuse old artifact handles');
  });
}
