// Regression test for Sprint 3 / M-2 audit fix: AppButton hit-target.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/shared/widgets/app_button.dart';

void main() {
  for (final entry in <(AppButtonSize, String)>[
    (AppButtonSize.sm, 'sm'),
    (AppButtonSize.md, 'md'),
    (AppButtonSize.lg, 'lg'),
  ]) {
    final (size, label) = entry;
    testWidgets('AppButton ($label) hit-target extends to 48dp vertical',
        (tester) async {
      var fired = false;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Center(
              child: SizedBox(
                width: 200,
                child: AppButton(
                  text: 'Tap me',
                  size: size,
                  onPressed: () => fired = true,
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pump(const Duration(milliseconds: 200));
      final visualHeight = size.height;
      final halfGap = (48 - visualHeight) / 2;
      final visibleCentre = tester.getCenter(find.text('Tap me'));
      final tapPoint = visibleCentre.translate(0, halfGap);
      await tester.tapAt(tapPoint);
      await tester.pump();
      expect(fired, isTrue);
    });
  }
}
