import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/theme/app_theme.dart';
import 'package:omniscribe_client/data/models/document_result.dart';
import 'package:omniscribe_client/data/models/feature_models.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import 'package:omniscribe_client/data/repositories/feature_repository.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';
import 'package:omniscribe_client/presentation/common/app_select.dart';
import 'package:omniscribe_client/presentation/workstation/modals/export_modal.dart';

class _MockFeatureRepository extends Mock implements FeatureRepository {}

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
    testWidgets('ExportFormat.docxTree exports successfully when artifacts present',
        (tester) async {
      when(() => mockRepo.exportDocxTree(any()))
          .thenAnswer((_) async => Uint8List.fromList([1, 2, 3, 4, 5]));

      final container = ProviderContainer(
        overrides: [
          featureRepositoryProvider.overrideWithValue(mockRepo),
        ],
      );
      addTearDown(container.dispose);

      // Setup workstation state with document and artifact handles
      container.read(workstationProvider.notifier).loadDocument(
            Uint8List.fromList([1, 2, 3]),
            'test.pdf',
          );
      container.read(workstationProvider.notifier).setTextArtifact(
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
                .having((r) => r.textArtifactToken, 'textArtifactToken', 'tok-abc'),
          ))).called(1);

      expect(
        find.textContaining('DOCX Tree'),
        findsWidgets,
      );
    });

    testWidgets('ExportFormat.docxTree shows error when text artifact is missing',
        (tester) async {
      final container = ProviderContainer(
        overrides: [
          featureRepositoryProvider.overrideWithValue(mockRepo),
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
        find.text('Text artifact not available. Please run OCR processing first.'),
        findsOneWidget,
      );
    });

    testWidgets('ExportFormat.searchablePdf shows ready message when loadedBytes present',
        (tester) async {
      final container = ProviderContainer(
        overrides: [
          featureRepositoryProvider.overrideWithValue(mockRepo),
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

      expect(
        find.textContaining('Searchable PDF'),
        findsWidgets,
      );
    });

    testWidgets('shows flagged-block count when trust summary reports flags',
        (tester) async {
      final container = ProviderContainer(
        overrides: [
          featureRepositoryProvider.overrideWithValue(mockRepo),
        ],
      );
      addTearDown(container.dispose);

      container.read(workstationProvider.notifier).loadDocument(
            Uint8List.fromList([1, 2, 3]),
            'scan.pdf',
          );
      container.read(workstationProvider.notifier).setTrustSummary(
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
          featureRepositoryProvider.overrideWithValue(mockRepo),
        ],
      );
      addTearDown(container.dispose);

      container.read(workstationProvider.notifier).loadDocument(
            Uint8List.fromList([1, 2, 3]),
            'scan.pdf',
          );
      container.read(workstationProvider.notifier).setTrustSummary(
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
  });
}
