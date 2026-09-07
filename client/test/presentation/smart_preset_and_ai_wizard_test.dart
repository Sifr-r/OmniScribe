import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/data/models/models.dart';
import 'package:omniscribe_client/presentation/providers/ai_setup_wizard_modal.dart';
import 'package:omniscribe_client/presentation/workstation/controls/smart_preset_selector.dart';

Widget _wrapWithTheme(Widget child) {
  return ProviderScope(
    child: MaterialApp(
      theme: ThemeData.dark().copyWith(
        extensions: [AppColorScheme.dark()],
      ),
      home: Scaffold(
        body: SingleChildScrollView(
          child: SizedBox(
            width: 400,
            child: child,
          ),
        ),
      ),
    ),
  );
}

void main() {
  group('SmartPresetSelector Widget Tests', () {
    testWidgets('Renders all 6 presets with titles and badges', (tester) async {
      SmartPreset? selectedPreset;

      await tester.pumpWidget(
        _wrapWithTheme(
          SmartPresetSelector(
            settings: const ProcessSettings(),
            onPresetSelected: (p) => selectedPreset = p,
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Check titles
      expect(find.text('Smart Presets'), findsOneWidget);
      expect(find.text('Standard Document'), findsOneWidget);
      expect(find.text('Receipts & Invoices'), findsOneWidget);
      expect(find.text('Handwritten Notes'), findsOneWidget);
      expect(find.text('Archival / Faded Scan'), findsOneWidget);
      expect(find.text('Lightning Fast'), findsOneWidget);
      expect(find.text('Deep High-Accuracy'), findsOneWidget);

      // Tap on Receipts preset
      await tester.tap(find.text('Receipts & Invoices'));
      await tester.pumpAndSettle();

      expect(selectedPreset, equals(SmartPreset.receipt));
    });

    testWidgets('Shows Custom Settings badge when settings are modified',
        (tester) async {
      const customSettings = ProcessSettings(
        dpi: 350,
        denseThreshold: 220,
        deskew: true,
      );

      await tester.pumpWidget(
        _wrapWithTheme(
          SmartPresetSelector(
            settings: customSettings,
            onPresetSelected: (_) {},
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Custom Settings'), findsOneWidget);
    });

    testWidgets('Shows auto-detect banner when specialized filename is detected',
        (tester) async {
      SmartPreset? appliedPreset;

      await tester.pumpWidget(
        _wrapWithTheme(
          SmartPresetSelector(
            settings: const ProcessSettings(),
            filename: 'grocery_receipt_aug2026.pdf',
            onPresetSelected: (p) => appliedPreset = p,
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Banner should be visible
      expect(find.textContaining('Detected format:'), findsOneWidget);
      expect(find.text('Receipts & Invoices'), findsWidgets);
      expect(find.text('Apply'), findsOneWidget);

      // Tap apply button
      await tester.tap(find.text('Apply'));
      await tester.pumpAndSettle();

      expect(appliedPreset, equals(SmartPreset.receipt));
    });
  });

  group('AISetupWizardModal Widget Tests', () {
    testWidgets('Renders Step 1 with Offline and Cloud cards', (tester) async {
      await tester.pumpWidget(
        _wrapWithTheme(
          Builder(
            builder: (context) => ElevatedButton(
              onPressed: () => AISetupWizardModal.show(context),
              child: const Text('Open Wizard'),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Open Modal
      await tester.tap(find.text('Open Wizard'));
      await tester.pumpAndSettle();

      // Verify Modal Title & Step 1 Options
      expect(find.text('AI Engine Setup Wizard'), findsOneWidget);
      expect(find.text('Run 100% Offline (Free & Private)'), findsOneWidget);
      expect(find.text('Use Cloud AI (Fast & Accurate)'), findsOneWidget);
    });

    testWidgets('Navigates to Offline Setup (Step 2A) and back', (tester) async {
      await tester.pumpWidget(
        _wrapWithTheme(
          Builder(
            builder: (context) => ElevatedButton(
              onPressed: () => AISetupWizardModal.show(context),
              child: const Text('Open Wizard'),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('Open Wizard'));
      await tester.pumpAndSettle();

      // Select Offline mode
      await tester.tap(find.text('Run 100% Offline (Free & Private)'));
      await tester.pumpAndSettle();

      // Step 2A UI
      expect(find.text('LM Studio'), findsOneWidget);
      expect(find.text('Ollama'), findsOneWidget);
      expect(find.text('Test Connection'), findsOneWidget);

      // Back Link
      expect(find.text('Back to mode selection'), findsOneWidget);
      await tester.tap(find.text('Back to mode selection'));
      await tester.pumpAndSettle();

      // Returned to Step 1
      expect(find.text('How would you like to run the AI engine?'), findsOneWidget);
    });

    testWidgets('Navigates to Cloud Setup (Step 2B) and shows provider options',
        (tester) async {
      await tester.pumpWidget(
        _wrapWithTheme(
          Builder(
            builder: (context) => ElevatedButton(
              onPressed: () => AISetupWizardModal.show(context),
              child: const Text('Open Wizard'),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('Open Wizard'));
      await tester.pumpAndSettle();

      // Select Cloud mode
      await tester.tap(find.text('Use Cloud AI (Fast & Accurate)'));
      await tester.pumpAndSettle();

      // Step 2B UI
      expect(find.text('Connect Cloud AI Provider'), findsOneWidget);
      expect(find.textContaining('API Key'), findsWidgets);
      expect(find.text('Need an API key?'), findsOneWidget);
      expect(find.text('Open Console'), findsOneWidget);
    });

    testWidgets(
        'Offline Setup (Step 2A) includes model selection picker and Pick button',
        (tester) async {
      await tester.pumpWidget(
        _wrapWithTheme(
          Builder(
            builder: (context) => ElevatedButton(
              onPressed: () => AISetupWizardModal.show(context),
              child: const Text('Open Wizard'),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('Open Wizard'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Run 100% Offline (Free & Private)'));
      await tester.pumpAndSettle();

      expect(find.text('Model Name / ID'), findsOneWidget);
      expect(find.text('Pick'), findsOneWidget);

      await tester.ensureVisible(find.text('Pick'));
      await tester.tap(find.text('Pick'));
      await tester.pumpAndSettle();
      expect(find.text('allenai/olmocr-2-7b'), findsWidgets);
    });
  });
}
