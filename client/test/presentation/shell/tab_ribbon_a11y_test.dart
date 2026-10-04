// Regression test for Sprint 3 / H-2 audit fix: TabRibbon tab buttons and
// the theme toggle must expose ``Semantics(button: true)`` and the
// selection state (``Semantics(selected: ...)``) to screen readers.
//
// Before the fix, a screen reader announced the tab's visible text only
// ("Workstation") with no indication that it was the selected tab. The
// fix wraps the InkWell/GestureDetector body in a ``Semantics`` widget
// that explicitly sets ``button: true`` and ``selected: isSelected``.
//
// We use ``tester.getSemantics(...)`` to find the Semantics node and
// inspect its data via ``SemanticsNode.getSemanticsData()``. The
// assertions are scoped to the tab button only; the brand, provider
// pill, and other decorative widgets remain unlabeled for now (they
// are informational, not actionable).

import 'dart:ui' show Tristate;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/app/shell_state.dart';
import 'package:omniscribe_client/app/tab_ribbon.dart';

/// Wave 16 / flutter_riverpod 3.4: ``NotifierProvider.overrideWith`` now
/// requires a ``Notifier Function()`` — a Notifier subclass that overrides
/// ``build()`` to produce the desired initial state — instead of the old
/// ``StateProvider`` ``(ref) => value`` closure pattern.
class _ActiveTabWorkstation extends ActiveTabNotifier {
  @override
  AppTab build() => AppTab.workstation;
}

void main() {
  testWidgets(
      'TabRibbon exposes Semantics(button: true, selected: ...) for tabs',
      (tester) async {
    final container = ProviderContainer(
      overrides: [
        activeTabProvider.overrideWith(_ActiveTabWorkstation.new),
      ],
    );
    addTearDown(container.dispose);

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: const MaterialApp(
          home: Scaffold(
            body: TabRibbon(),
          ),
        ),
      ),
    );
    await tester.pump();

    final handle = tester.getSemantics(find.text('Workstation'));
    final node = handle.getSemanticsData();
    // Flutter 3.32 deprecation: ``hasFlag`` is replaced by reading the
    // ``SemanticsFlags`` instance on ``flagsCollection`` directly. Each
    // ``SemanticsFlag`` maps to a named field on ``SemanticsFlags`` —
    // ``isButton`` is a ``bool`` but ``isSelected`` is a ``Tristate``
    // (``isTrue`` / ``isFalse`` / ``none``), so we compare against the
    // ``Tristate`` value rather than a plain ``bool``.
    expect(node.flagsCollection.isButton, isTrue,
        reason: 'tab button must be announced as a button to screen readers');
    expect(node.flagsCollection.isSelected, Tristate.isTrue,
        reason: 'active tab must be announced with the selected state');

    final settingsHandle = tester.getSemantics(find.text('Settings'));
    final settingsNode = settingsHandle.getSemanticsData();
    expect(settingsNode.flagsCollection.isButton, isTrue);
    expect(settingsNode.flagsCollection.isSelected, Tristate.isFalse);
  });
}
