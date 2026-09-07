import 'dart:typed_data';

import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';

/// Repository for the Sprint 3 (RFC 002 §4 Option b, audit U12)
/// "try with sample PDF" affordance. Fetches a canonical fixture
/// PDF from the server's
/// ``src/omniscribe/resources/sample_pdfs/`` directory and returns
/// the raw bytes — the Workstation screen hands those bytes to
/// the existing ``OcrRepository.processOcrAsync`` flow so the
/// sample PDF goes through the same job pipeline as a user-uploaded
/// file.
///
/// The list of fixture names is the server-side
/// ``ALLOWED_SAMPLE_PDFS`` allowlist (see
/// ``omniscribe.plugins.sample_pdfs``). Adding a fixture server-side
/// also requires adding it here so the Flutter UI can offer it.
class SamplePdfRepository {
  SamplePdfRepository(this._apiClient);

  final ApiClient _apiClient;

  /// The five canonical fixture names, mirrored from the
  /// server-side ``ALLOWED_SAMPLE_PDFS`` allowlist. Kept in
  /// lockstep via the contract note on
  /// ``ApiConstants.samplePdf``.
  static const List<String> availableFixtures = <String>[
    'digital.pdf',
    'handwritten.pdf',
    'hybrid.pdf',
    'dense.pdf',
    'notes.pdf',
  ];

  /// The default fixture for the "Try sample PDF" button.
  /// ``digital.pdf`` is the most representative: small (126 KB),
  /// fast to OCR, clear text on every page, good first impression.
  static const String defaultFixture = 'digital.pdf';

  /// Fetch a canonical fixture PDF as raw bytes. The ``name``
  /// argument must be in [availableFixtures]; the server enforces
  /// the same allowlist (404 on unknown names, 4xx on path
  /// traversal). Throws an [ApiException] on non-2xx.
  Future<Uint8List> fetchSamplePdf(String name) async {
    return _apiClient.getBytes(
      ApiConstants.samplePdf(name),
      // The route is path-prefix-exempt in
      // ``omniscribe.middleware.auth.EXEMPT_PATH_PREFIXES``, so no
      // Authorization header is required (and the loopback
      // Profile 1 Flutter client has no token to send).
      headers: const <String, String>{},
    );
  }

  /// Convenience: fetch the [defaultFixture]. Equivalent to
  /// ``fetchSamplePdf(defaultFixture)`` but reads more naturally
  /// at the call site.
  Future<Uint8List> fetchDefaultSamplePdf() => fetchSamplePdf(defaultFixture);
}
