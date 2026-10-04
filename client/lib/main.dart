import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/app/app_shell.dart';
import 'package:omniscribe_client/app/shell_state.dart';
import 'package:omniscribe_client/core/theme/app_theme.dart';
import 'package:omniscribe_client/features/settings/settings_notifier.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Debug affordance: ``?a11y=1`` forces the semantics tree on so headless
  // browser tooling (docs screenshot capture, CI UI checks) can drive the
  // app without a real screen reader attached.
  if (Uri.base.queryParameters['a11y'] == '1') {
    SemanticsBinding.instance.ensureSemantics();
  }
  runApp(
    const ProviderScope(
      child: OmniScribeApp(),
    ),
  );
}

class OmniScribeApp extends ConsumerStatefulWidget {
  const OmniScribeApp({super.key});

  @override
  ConsumerState<OmniScribeApp> createState() => _OmniScribeAppState();
}

class _OmniScribeAppState extends ConsumerState<OmniScribeApp> {
  @override
  void initState() {
    super.initState();
    // Kick off the initial config fetch once the notifier is available.
    Future.microtask(
      () => ref.read(settingsStateProvider.notifier).load(),
    );
    // Probe the backend once at startup so the shell health badge reflects
    // the real connection state instead of staying on its "Checking" seed.
    Future.microtask(
      () => ref.read(serverHealthProvider.notifier).checkHealth(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isDarkMode =
        ref.watch(settingsStateProvider.select((s) => s.isDarkMode));

    return MaterialApp(
      title: 'OmniScribe',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.lightTheme,
      darkTheme: AppTheme.darkTheme,
      themeMode: isDarkMode ? ThemeMode.dark : ThemeMode.light,
      home: const AppShell(),
    );
  }
}
