/** Minimal, dependency-free CSV parser (RFC 4180-ish).
 *
 *  Handles quoted fields, escaped quotes ("" inside quotes), and commas /
 *  newlines inside quoted fields. Accepts both \n and \r\n line endings. */

export interface ParsedCsv {
  headers: string[];
  /** Each row keyed by the (verbatim) header. */
  rows: Record<string, string>[];
  /** Raw field matrix (excluding the header row), useful for previews. */
  matrix: string[][];
}

function splitRecords(text: string): string[][] {
  const records: string[][] = [];
  let field = "";
  let record: string[] = [];
  let inQuotes = false;

  // Normalize a trailing newline so we don't emit a spurious empty record.
  const input = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

  for (let i = 0; i < input.length; i++) {
    const char = input[i];

    if (inQuotes) {
      if (char === '"') {
        if (input[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += char;
      }
      continue;
    }

    if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      record.push(field);
      field = "";
    } else if (char === "\n") {
      record.push(field);
      records.push(record);
      record = [];
      field = "";
    } else {
      field += char;
    }
  }

  // Flush the final field/record if the file didn't end with a newline.
  if (field.length > 0 || record.length > 0) {
    record.push(field);
    records.push(record);
  }

  return records;
}

export function parseCsv(text: string): ParsedCsv {
  const records = splitRecords(text).filter(
    // Drop fully-empty lines (e.g. a blank line between rows).
    (r) => !(r.length === 1 && r[0].trim() === ""),
  );

  if (records.length === 0) {
    return { headers: [], rows: [], matrix: [] };
  }

  const headers = records[0].map((h) => h.trim());
  const matrix = records.slice(1);
  const rows = matrix.map((cells) => {
    const row: Record<string, string> = {};
    headers.forEach((header, i) => {
      row[header] = (cells[i] ?? "").trim();
    });
    return row;
  });

  return { headers, rows, matrix };
}
