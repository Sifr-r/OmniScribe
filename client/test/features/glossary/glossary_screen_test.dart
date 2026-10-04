import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/theme/app_theme.dart';
import 'package:omniscribe_client/features/glossary/glossary_models.dart';
import 'package:omniscribe_client/features/glossary/glossary_screen.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';

class _MockGlossaryRepository extends Mock implements GlossaryRepository {}

void main() {
  Widget buildScreen(_MockGlossaryRepository repo) {
    return ProviderScope(
      overrides: [glossaryRepositoryProvider.overrideWithValue(repo)],
      child: MaterialApp(
        theme: AppTheme.darkTheme,
        home: const GlossaryScreen(),
      ),
    );
  }

  Future<_MockGlossaryRepository> pumpAt(
    WidgetTester tester,
    Size surface,
  ) async {
    tester.view.physicalSize = surface;
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final repo = _MockGlossaryRepository();
    when(() => repo.getGlossaryLibraries())
        .thenAnswer((_) async => <GlossaryListItem>[]);

    await tester.pumpWidget(buildScreen(repo));
    await tester.pumpAndSettle();
    return repo;
  }

  testWidgets('header does not overflow at a narrow width', (tester) async {
    // The title block and the trailing actions share one row. The title column
    // must therefore stay flexible: without it the title, badge and subtitle
    // take their full intrinsic width and push the actions off the surface.
    await pumpAt(tester, const Size(420, 900));

    expect(
      tester.takeException(),
      isNull,
      reason: 'a narrow surface must ellipsize the title, not overflow',
    );
  });

  testWidgets('trailing actions stay reachable when narrow', (tester) async {
    const width = 420.0;
    await pumpAt(tester, const Size(width, 900));

    final import = find.text('Import glossary');
    expect(import, findsOneWidget,
        reason: 'the header action must survive a constrained width');
    expect(find.text('Terminology Glossary'), findsOneWidget);

    // The action cluster is rigid, so below the inline breakpoint it drops to
    // its own line and scrolls horizontally. The action must therefore start
    // out of view and come into view by scrolling — never be clipped away.
    final headerScroll = find.byWidgetPredicate(
      (w) => w is SingleChildScrollView && w.scrollDirection == Axis.horizontal,
    );
    expect(headerScroll, findsOneWidget,
        reason: 'the rigid action cluster must scroll rather than overflow');
    expect(tester.getCenter(import).dx, greaterThan(width),
        reason: 'the action starts scrolled out of the narrow viewport');

    await tester.drag(headerScroll, const Offset(-400, 0));
    await tester.pumpAndSettle();

    expect(tester.getCenter(import).dx, lessThanOrEqualTo(width),
        reason: 'scrolling must bring the header action into view');
  });

  testWidgets('header renders normally at a wide width', (tester) async {
    await pumpAt(tester, const Size(1400, 1000));

    expect(tester.takeException(), isNull);
    expect(find.text('Terminology Glossary'), findsOneWidget);
    expect(find.text('Import glossary'), findsOneWidget);
  });
}
