import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/app/shell_state.dart';
import 'package:omniscribe_client/app/tab_ribbon.dart';
import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/features/documents/export_modal.dart';
import 'package:omniscribe_client/features/documents/extraction_screen.dart';
import 'package:omniscribe_client/features/glossary/glossary_screen.dart';
import 'package:omniscribe_client/features/jobs/job_history_screen.dart';
import 'package:omniscribe_client/features/settings/settings_screen.dart';
import 'package:omniscribe_client/features/transcription/transcription_screen.dart';
import 'package:omniscribe_client/features/translation/translation_screen.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/features/workstation/workstation_screen.dart';
import 'package:omniscribe_client/shared/widgets/auth_required_banner.dart';

class AppShell extends ConsumerWidget {
  const AppShell({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final activeTab = ref.watch(activeTabProvider);
    final colors = context.colors;

    Widget currentScreen;
    switch (activeTab) {
      case AppTab.workstation:
        currentScreen = const WorkstationScreen();
        break;
      case AppTab.translation:
        currentScreen = const TranslationScreen();
        break;
      case AppTab.transcription:
        currentScreen = const TranscriptionScreen();
        break;
      case AppTab.extraction:
        currentScreen = const ExtractionScreen();
        break;
      case AppTab.glossary:
        currentScreen = const GlossaryScreen();
        break;
      case AppTab.jobs:
        currentScreen = const JobHistoryScreen();
        break;
      case AppTab.settings:
        currentScreen = const SettingsScreen();
        break;
    }

    return CallbackShortcuts(
      bindings: {
        const SingleActivator(LogicalKeyboardKey.digit1, control: true): () {
          ref.read(activeTabProvider.notifier).set(AppTab.workstation);
        },
        const SingleActivator(LogicalKeyboardKey.digit2, control: true): () {
          ref.read(activeTabProvider.notifier).set(AppTab.translation);
        },
        const SingleActivator(LogicalKeyboardKey.digit3, control: true): () {
          ref.read(activeTabProvider.notifier).set(AppTab.transcription);
        },
        const SingleActivator(LogicalKeyboardKey.digit4, control: true): () {
          ref.read(activeTabProvider.notifier).set(AppTab.extraction);
        },
        const SingleActivator(LogicalKeyboardKey.digit5, control: true): () {
          ref.read(activeTabProvider.notifier).set(AppTab.glossary);
        },
        const SingleActivator(LogicalKeyboardKey.digit6, control: true): () {
          ref.read(activeTabProvider.notifier).set(AppTab.jobs);
        },
        const SingleActivator(LogicalKeyboardKey.digit7, control: true): () {
          ref.read(activeTabProvider.notifier).set(AppTab.settings);
        },
        const SingleActivator(LogicalKeyboardKey.keyS, control: true): () {
          final wsState = ref.read(workstationProvider);
          if (wsState.hasDocument) {
            ExportModal.show(context);
          }
        },
        // Phase A: Ctrl+O bumps the workstation's filePickSignal so the
        // mounted UploadDropzone (the only widget that owns the native
        // file picker) opens it. Guarded on workstation tab so other tabs
        // don't trigger a picker dialog.
        const SingleActivator(LogicalKeyboardKey.keyO, control: true): () {
          final activeTab = ref.read(activeTabProvider);
          if (activeTab == AppTab.workstation) {
            ref.read(workstationProvider.notifier).incrementFilePick();
          }
        },
        // Phase A: Ctrl+Enter kicks off OCR with default settings, but only
        // on the workstation tab AND when a document is already loaded. The
        // dock's tweaked ProcessSettings are intentionally ignored here —
        // AppShell shortcuts are a coarse shortcut layer.
        const SingleActivator(LogicalKeyboardKey.enter, control: true): () {
          final activeTab = ref.read(activeTabProvider);
          final wsState = ref.read(workstationProvider);
          if (activeTab == AppTab.workstation && wsState.hasDocument) {
            // ignore: unawaited_futures
            ref.read(workstationProvider.notifier).processCurrentDocument();
          }
        },
      },
      child: Focus(
        autofocus: true,
        child: Scaffold(
          backgroundColor: colors.background,
          body: SafeArea(
            child: Column(
              children: [
                const AuthRequiredBanner(),
                const TabRibbon(),
                Expanded(child: currentScreen),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
