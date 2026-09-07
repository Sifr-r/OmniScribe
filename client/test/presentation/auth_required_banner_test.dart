import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/presentation/common/auth_required_banner.dart';

/// Wave 16 / flutter_riverpod 3.4: ``NotifierProvider.overrideWith`` now
/// requires a ``Notifier Function()`` — a Notifier subclass that overrides
/// ``build()`` — instead of the old ``StateProvider`` ``(ref) => value``
/// closure pattern. We define a tiny [AuthRequiredNotifier] subclass for
/// the "always true" test scenario and reuse it across the two tests.
class _AuthRequiredTrue extends AuthRequiredNotifier {
  @override
  bool build() => true;
}

void main() {
  testWidgets('hides by default', (tester) async {
    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(home: Scaffold(body: AuthRequiredBanner())),
      ),
    );
    expect(find.text('Authentication required'), findsNothing);
  });

  testWidgets('shows when authRequiredProvider is true', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authRequiredProvider.overrideWith(_AuthRequiredTrue.new),
        ],
        child: const MaterialApp(
          home: Scaffold(body: AuthRequiredBanner()),
        ),
      ),
    );
    await tester.pump();
    expect(find.text('Authentication required'), findsOneWidget);
    expect(find.text('Open Settings'), findsOneWidget);
    expect(
      find.textContaining('Check the bearer token in Settings'),
      findsOneWidget,
    );
  });

  testWidgets('dismiss button clears the flag', (tester) async {
    final container = ProviderContainer(
      overrides: [authRequiredProvider.overrideWith(_AuthRequiredTrue.new)],
    );
    addTearDown(container.dispose);

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: const MaterialApp(
          home: Scaffold(body: AuthRequiredBanner()),
        ),
      ),
    );
    await tester.pump();
    expect(container.read(authRequiredProvider), isTrue);

    await tester.tap(find.byIcon(Icons.close));
    await tester.pump();
    expect(container.read(authRequiredProvider), isFalse);
  });
}
