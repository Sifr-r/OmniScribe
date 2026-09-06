import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/data/providers/settings_state.dart';

void main() {
  group('SettingsState.initial', () {
    test('returns sane defaults: not loading, no runtime config, no error', () {
      const state = SettingsState.initial();
      expect(state.isLoading, isFalse);
      expect(state.runtimeConfig, isNull);
      expect(state.error, isNull);
      expect(state.activeProviderId, 'openai');
      expect(state.ocrModels, isEmpty);
      expect(state.serverBaseUrl, 'http://127.0.0.1:8000');
      expect(state.useAsync, isFalse);
      expect(state.isDarkMode, isFalse);
    });
  });

  group('SettingsState.copyWith', () {
    test('preserves untouched fields', () {
      const before = SettingsState.initial();
      final after = before.copyWith(isLoading: true);
      expect(after.isLoading, isTrue);
      expect(after.runtimeConfig, before.runtimeConfig);
      expect(after.error, before.error);
      expect(after.activeProviderId, before.activeProviderId);
    });

    test('clearError: null error is preserved when explicit null passed', () {
      const before = SettingsState.initial();
      final after = before.copyWith(clearError: true);
      expect(after.error, isNull);
    });
  });
}
