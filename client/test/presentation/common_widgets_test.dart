import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_theme.dart';
import 'package:omniscribe_client/shared/widgets/app_badge.dart';
import 'package:omniscribe_client/shared/widgets/app_button.dart';
import 'package:omniscribe_client/shared/widgets/app_card.dart';
import 'package:omniscribe_client/shared/widgets/app_input.dart';
import 'package:omniscribe_client/shared/widgets/app_toggle.dart';
import 'package:omniscribe_client/shared/widgets/toast_service.dart';

void main() {
  Widget buildTestableWidget(Widget child) {
    return ProviderScope(
      child: MaterialApp(
        theme: AppTheme.darkTheme,
        home: Scaffold(
          body: Center(child: child),
        ),
      ),
    );
  }

  group('AppButton Tests', () {
    testWidgets('Renders label and triggers onPressed callback',
        (WidgetTester tester) async {
      bool tapped = false;
      await tester.pumpWidget(
        buildTestableWidget(
          AppButton(
            text: 'Run OCR',
            onPressed: () => tapped = true,
          ),
        ),
      );

      expect(find.text('Run OCR'), findsOneWidget);
      await tester.tap(find.text('Run OCR'));
      await tester.pumpAndSettle();
      expect(tapped, isTrue);
    });

    testWidgets('Shows loading spinner and disables callback when loading=true',
        (WidgetTester tester) async {
      bool tapped = false;
      await tester.pumpWidget(
        buildTestableWidget(
          AppButton(
            text: 'Process',
            loading: true,
            onPressed: () => tapped = true,
          ),
        ),
      );

      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      await tester.tap(find.byType(AppButton));
      expect(tapped, isFalse);
    });

    testWidgets('Respects disabled=true and prevents tap',
        (WidgetTester tester) async {
      bool tapped = false;
      await tester.pumpWidget(
        buildTestableWidget(
          AppButton(
            text: 'Disabled Action',
            disabled: true,
            onPressed: () => tapped = true,
          ),
        ),
      );

      await tester.tap(find.text('Disabled Action'));
      expect(tapped, isFalse);
    });
  });

  group('AppBadge Tests', () {
    testWidgets('Renders badge with label and dot indicator',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        buildTestableWidget(
          const AppBadge(
            label: 'HIGH CONFIDENCE',
            variant: AppBadgeVariant.success,
            dot: true,
          ),
        ),
      );

      expect(find.text('HIGH CONFIDENCE'), findsOneWidget);
    });
  });

  group('AppToggle Tests', () {
    testWidgets('Toggles state when clicked', (WidgetTester tester) async {
      bool value = false;
      await tester.pumpWidget(
        buildTestableWidget(
          StatefulBuilder(
            builder: (BuildContext context, StateSetter setState) {
              return AppToggle(
                label: 'Denoise Image',
                value: value,
                onChanged: (bool v) => setState(() => value = v),
              );
            },
          ),
        ),
      );

      expect(find.text('Denoise Image'), findsOneWidget);
      await tester.tap(find.text('Denoise Image'));
      await tester.pumpAndSettle();
      expect(value, isTrue);
    });
  });

  group('AppInput Tests', () {
    testWidgets('Accepts text input and updates controller',
        (WidgetTester tester) async {
      final controller = TextEditingController();
      await tester.pumpWidget(
        buildTestableWidget(
          AppInput(
            label: 'API Base',
            controller: controller,
            placeholder: 'http://localhost:8000',
          ),
        ),
      );

      expect(find.text('API Base'), findsOneWidget);
      await tester.enterText(
          find.byType(TextFormField), 'https://api.openai.com/v1');
      expect(controller.text, 'https://api.openai.com/v1');
    });

    testWidgets('Displays error message when errorText is provided',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        buildTestableWidget(
          const AppInput(
            label: 'Endpoint',
            errorText: 'Invalid URL format',
          ),
        ),
      );

      expect(find.text('Invalid URL format'), findsOneWidget);
    });
  });

  group('AppCard Tests', () {
    testWidgets('Renders card title, subtitle, headerAction, and child content',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        buildTestableWidget(
          const AppCard(
            title: 'OCR Settings',
            subtitle: 'Engine parameters',
            headerAction: Text('Edit'),
            child: Text('Card Content Area'),
          ),
        ),
      );

      expect(find.text('OCR Settings'), findsOneWidget);
      expect(find.text('Engine parameters'), findsOneWidget);
      expect(find.text('Edit'), findsOneWidget);
      expect(find.text('Card Content Area'), findsOneWidget);
    });
  });

  group('ToastService Tests', () {
    test('ToastNotifier adds and dismisses toasts cleanly', () {
      // Wave 16 / flutter_riverpod 3.4: ``Notifier.state`` is no longer
      // exposed as a getter on the notifier instance. Read the value via
      // the provider instead.
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final notifier = container.read(toastProvider.notifier);

      expect(container.read(toastProvider), isEmpty);

      final id = notifier.success('Operation succeeded');
      final afterAdd = container.read(toastProvider);
      expect(afterAdd.length, 1);
      expect(afterAdd.first.message, 'Operation succeeded');
      expect(afterAdd.first.level, ToastLevel.success);

      notifier.dismissToast(id);
      expect(container.read(toastProvider), isEmpty);

      notifier.info('First');
      notifier.warning('Second');
      expect(container.read(toastProvider).length, 2);

      notifier.clearAll();
      expect(container.read(toastProvider), isEmpty);
    });
  });
}
