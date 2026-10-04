import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/theme/app_theme.dart';
import 'package:omniscribe_client/data/models/models.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';
import 'package:omniscribe_client/features/jobs/job_orchestration_notifier.dart';
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
  });

  Widget buildExportModal(ProviderContainer container) {
    return UncontrolledProviderScope(
      container: container,
      child: MaterialApp(
        theme: AppTheme.darkTheme,
        home: const Scaffold(
          body: Center(child: ExportModal()),
        ),
      ),
    );
  }

  group('ExportModal Tests', () {
    testWidgets(
        'ExportFormat.docxTree exports successfully when artifacts present',
        (tester) async {
      when(() => mockRepo.exportDocxTree(any()))
          .thenAnswer((_) async => Uint8List.fromList([1, 2, 3, 4, 5]));

      final container = ProviderContainer(
        overrides: [
          translationRepositoryProvider.overrideWithValue(mockRepo),
          transcriptionRepositoryProvider.overrideWithValue(mockRepo),
          glossaryRepositoryProvider.overrideWithValue(mockRepo),
          documentRepositoryProvider.overrideWithValue(mockRepo),
        ],
      );
      addTearDown(container.dispose);

      // Setup workstation state with document and artifact handles
      container.read(workstationProvider.notifier).loadDocument(
            Uint8List.fromList([1, 2, 3]),
            'test.pdf',
          );
      container.read(jobOrchestrationProvider.notifier).setTextArtifact(
            textArtifactId: 'art-123',
            textArtifactToken: 'tok-abc',
          );

      await tester.pumpWidget(buildExportModal(container));
      await tester.pumpAndSettle();

      // Switch target file format to DOCX Tree Layout
      final selectFinder = find.byType(AppSelect<ExportFormat>);
      expect(selectFinder, findsOneWidget);

      // Select docxTree via widget
      await tester.tap(selectFinder);
      await tester.pumpAndSettle();

      final docxTreeItem = find.textContaining('DOCX Tree Layout');
      expect(docxTreeItem, findsWidgets);
      await tester.tap(docxTreeItem.last);
      await tester.pumpAndSettle();

      final exportButton = find.widgetWithText(AppButton, 'Export Document');
      expect(exportButton, findsOneWidget);
      await tester.tap(exportButton);
      await tester.pumpAndSettle();

      verify(() => mockRepo.exportDocxTree(any(
            that: isA<ExportBlockTreeRequest>()
                .having((r) => r.textArtifactId, 'textArtifactId', 'art-123')
                .having(
                    (r) => r.textArtifactToken, 'textArtifactToken', 'tok-abc'),
          ))).called(1);

      expect(
        find.textContaining('DOCX Tree'),
        findsWidgets,
      );
    });

    testWidgets(
        'ExportFormat.docxTree shows error when text artifact is missing',
        (tester) async {
      final container = ProviderContainer(
        overrides: [
          translationRepositoryProvider.overrideWithValue(mockRepo),
          transcriptionRepositoryProvider.overrideWithValue(mockRepo),
          glossaryRepositoryProvider.overrideWithValue(mockRepo),
          documentRepositoryProvider.overrideWithValue(mockRepo),
        ],
      );
      addTearDown(container.dispose);

      // Load document without textArtifactId/token
      container.read(workstationProvider.notifier).loadDocument(
            Uint8List.fromList([1, 2, 3]),
            'test.pdf',
          );

      await tester.pumpWidget(buildExportModal(container));
      await tester.pumpAndSettle();

      // Open select and pick docxTree
      await tester.tap(find.byType(AppSelect<ExportFormat>));
      await tester.pumpAndSettle();

      final docxTreeItem = find.textContaining('DOCX Tree Layout');
      await tester.tap(docxTreeItem.last);
      await tester.pumpAndSettle();

      // Tap export
      await tester.tap(find.widgetWithText(AppButton, 'Export Document'));
      await tester.pumpAndSettle();

      verifyNever(() => mockRepo.exportDocxTree(any()));
      expect(
        find.text(
            'Text artifact not available. Please run OCR processing first.'),
        findsOneWidget,
      );
    });

    testWidgets('ExportFormat.searchablePdf rejects an unprocessed source PDF',
        (tester) async {
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
            Uint8List(2048),
            'sample.pdf',
          );

      await tester.pumpWidget(buildExportModal(container));
      await tester.pumpAndSettle();

      // Default format is searchablePdf
      await tester.tap(find.widgetWithText(AppButton, 'Export Document'));
      await tester.pumpAndSettle();

      expect(find.text('NO DATA'), findsOneWidget);
      expect(find.text('PDF not available. Please run OCR processing first.'),
          findsOneWidget);
    });

    testWidgets('shows flagged-block count when trust summary reports flags',
        (tester) async {
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
          );
      container.read(jobOrchestrationProvider.notifier).setTrustSummary(
            const TrustSummary(
              blockCount: 10,
              scoredCount: 10,
              flaggedCount: 3,
              average: 0.71,
            ),
          );

      await tester.pumpWidget(buildExportModal(container));
      await tester.pumpAndSettle();

      expect(find.text('3 blocks flagged for review'), findsOneWidget);
    });

    testWidgets('omits flagged-block line when trust summary has no flags',
        (tester) async {
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
          );
      container.read(jobOrchestrationProvider.notifier).setTrustSummary(
            const TrustSummary(
              blockCount: 10,
              scoredCount: 10,
              flaggedCount: 0,
              average: 0.95,
            ),
          );

      await tester.pumpWidget(buildExportModal(container));
      await tester.pumpAndSettle();

      expect(find.textContaining('flagged for review'), findsNothing);
    });

    testWidgets(
        'ExportFormat.docx rejects whitespace-only text before API calls',
        (tester) async {
      // A filename is not recognized document content.
      when(() => mockRepo.exportDocx(any())).thenAnswer(
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
          );
      container.read(workstationProvider.notifier).addOrUpdateBBox(
            0,
            const BBoxItem(
              blockId: 'p0_b0',
              page: 0,
              block: 0,
              bbox: [0.0, 0.0, 1.0, 0.1],
              text: '   ',
            ),
          );
      container.read(workstationProvider.notifier).addOrUpdateBBox(
            0,
            const BBoxItem(
              blockId: 'p0_b1',
              page: 0,
              block: 1,
              bbox: [0.0, 0.1, 1.0, 0.2],
              text: '\n\n',
            ),
          );

      await tester.pumpWidget(buildExportModal(container));
      await tester.pumpAndSettle();

      // Select Word Document (default is Searchable PDF).
      await tester.tap(find.byType(AppSelect<ExportFormat>));
      await tester.pumpAndSettle();
      final docxItem = find.textContaining('Word Document');
      await tester.tap(docxItem.last);
      await tester.pumpAndSettle();

      await tester.tap(find.widgetWithText(AppButton, 'Export Document'));
      await tester.pumpAndSettle();

      verifyNever(() => mockRepo.exportDocx(any()));
      expect(
        find.text(
            'Recognized text not available. Please run OCR processing first.'),
        findsOneWidget,
      );
    });
  });
}
