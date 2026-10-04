/// Read required wire fields without coercing malformed responses into success.
String jsonString(Map<String, dynamic> json, String field) {
  final value = json[field];
  if (value is! String) {
    throw FormatException('Expected a string for "$field".');
  }
  return value;
}

double jsonDouble(Map<String, dynamic> json, String field) {
  final value = json[field];
  if (value is! num || !value.isFinite) {
    throw FormatException('Expected a finite number for "$field".');
  }
  return value.toDouble();
}

int jsonInt(Map<String, dynamic> json, String field) {
  final value = json[field];
  if (value is! int) {
    throw FormatException('Expected an integer for "$field".');
  }
  return value;
}
