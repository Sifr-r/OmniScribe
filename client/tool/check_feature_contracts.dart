import '../lib/features/documents/documents_models.dart';
import '../lib/features/glossary/glossary_models.dart';
import '../lib/features/jobs/job_record.dart';
import '../lib/features/transcription/transcription_models.dart';
import '../lib/features/translation/translation_models.dart';

bool rejects(void Function() parse) {
  try {
    parse();
    return false;
  } on FormatException {
    return true;
  }
}

void main() {
  for (final engine in TranscriptionEngineType.values) {
    assert(TranscriptionEngineType.fromString(engine.value) == engine);
  }
  assert(rejects(() => TranscriptionEngineType.fromString('unknown')));
  for (final format in GlossaryFormat.values) {
    assert(GlossaryFormat.fromString(format.value) == format);
  }
  assert(rejects(() => TranslationResponse.fromJson({})));
  assert(rejects(() => AsyncSubmitResponse.fromJson({'job_id': 'id'})));
  assert(rejects(() => AsyncSubmitResponse.fromJson({'status': 'pending'})));
  assert(rejects(() => TranslationResponse.fromJson({'translated_text': 17})));
  assert(TranslationResponse.fromJson({'translated_text': ''})
      .translatedText
      .isEmpty);
  assert(
      rejects(() => TranslationJobStatusResponse.fromJson({'job_id': 'id'})));
  assert(TranslationJobStatusResponse.fromJson(
          {'job_id': 'id', 'status': 'pending'}).state ==
      'pending');
  assert(rejects(() => ExtractionResponse.fromJson({'wrong_envelope': true})));
  assert(ExtractionResponse.fromJson({'extracted_data': null}).extractedData ==
      null);
  assert(rejects(() => GlossaryEntry.fromJson({'source': 'term'})));
  final segment = {'id': 1, 'start': 0, 'end': 1.0, 'text': 'speech'};
  assert(TranscriptionSegment.fromJson(segment).end == 1.0);
  assert(rejects(() => TranscriptionSegment.fromJson({...segment, 'id': 1.5})));
  assert(rejects(
      () => TranscriptionSegment.fromJson({...segment, 'start': double.nan})));
  assert(rejects(
      () => TranscriptionSegment.fromJson({...segment, 'text': false})));
  assert(rejects(() => TranscriptionResponse.fromJson({
        'text': 'speech',
        'segments': [false]
      })));
  assert(rejects(() => TranscriptionResponse.fromJson(
      {'text': 'speech', 'duration': double.infinity})));
  print('Feature JSON contracts passed.');
}
