import * as Papa from 'papaparse';
import * as XLSX from 'xlsx';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ParsedFile {
  name: string;
  type: string; // mime type
  size: number;
  text: string; // extracted text content
  summary: string; // brief summary for display
  rows?: number; // for tabular data
  columns?: string[]; // column headers for tabular data
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Format a byte count into a human-readable string.
 */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/**
 * Truncate a string to `maxLen` characters, appending "…" when truncated.
 */
function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen) + '…';
}

/**
 * Escape markdown table cell content (pipes & line-breaks).
 */
function escapeCell(value: unknown): string {
  const s = String(value ?? '');
  return s.replace(/\|/g, '\\|').replace(/\n/g, ' ').replace(/\r/g, '');
}

/**
 * Convert an array of row objects into a markdown table string.
 *
 * - Only `headers` columns are included (in that order).
 * - Cell values are truncated to `maxCellLen` characters.
 * - At most `maxRows` data rows are rendered.
 */
function arrayToMarkdownTable(
  headers: string[],
  rows: Record<string, unknown>[],
  maxRows = 50,
  maxCellLen = 50,
): string {
  const limitedRows = rows.slice(0, maxRows);

  // Header row
  const headerLine = '| ' + headers.map((h) => escapeCell(h)).join(' | ') + ' |';
  // Separator row
  const sepLine = '| ' + headers.map(() => '------').join(' | ') + ' |';
  // Data rows
  const dataLines = limitedRows.map(
    (row) =>
      '| ' +
      headers
        .map((h) => escapeCell(truncate(String(row[h] ?? ''), maxCellLen)))
        .join(' | ') +
      ' |',
  );

  const lines = [headerLine, sepLine, ...dataLines];

  if (rows.length > maxRows) {
    lines.push(`\n... 共 ${rows.length} 行，仅显示前 ${maxRows} 行`);
  }

  return lines.join('\n');
}

/**
 * Read a File as text using the FileReader API (Promise-based).
 */
function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

/**
 * Read a File as an ArrayBuffer using the FileReader API (Promise-based).
 */
function readFileAsArrayBuffer(file: File): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

// ---------------------------------------------------------------------------
// Parsers per file type
// ---------------------------------------------------------------------------

/**
 * Parse a CSV or TSV file using PapaParse.
 */
async function parseCsv(file: File): Promise<ParsedFile> {
  const text = await readFileAsText(file);

  return new Promise<ParsedFile>((resolve, reject) => {
    Papa.parse(text, {
      header: true,
      skipEmptyLines: true,
      complete(results) {
        const columns = results.meta.fields ?? [];
        const rows = results.data as Record<string, unknown>[];
        const markdown = arrayToMarkdownTable(columns, rows);
        const summary = `CSV 文件，${rows.length} 行 × ${columns.length} 列`;

        resolve({
          name: file.name,
          type: file.type || 'text/csv',
          size: file.size,
          text: markdown,
          summary,
          rows: rows.length,
          columns,
        });
      },
      error(err) {
        reject(err);
      },
    });
  });
}

/**
 * Parse an Excel file (XLSX / XLS) using the xlsx library.
 */
async function parseExcel(file: File): Promise<ParsedFile> {
  const buffer = await readFileAsArrayBuffer(file);
  const workbook = XLSX.read(buffer, { type: 'array' });
  const sheetNames = workbook.SheetNames;
  const firstSheetName = sheetNames[0];
  const worksheet = workbook.Sheets[firstSheetName];

  // Convert to array-of-objects
  const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(worksheet, {
    defval: '',
  });

  // Derive column headers from the first row (or all rows if empty)
  const columns =
    rows.length > 0
      ? Object.keys(rows[0])
      : (XLSX.utils.sheet_to_json<string[]>(worksheet, { header: 1 })[0] as string[] | undefined) ?? [];

  const markdown = arrayToMarkdownTable(columns, rows);
  const summary = `Excel 文件，${sheetNames.length} 个工作表，主表 ${rows.length} 行 × ${columns.length} 列`;

  return {
    name: file.name,
    type: file.type || 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    size: file.size,
    text: markdown,
    summary,
    rows: rows.length,
    columns,
  };
}

/**
 * Parse a JSON file.
 */
async function parseJson(file: File): Promise<ParsedFile> {
  const text = await readFileAsText(file);
  const parsed: unknown = JSON.parse(text);

  // Array of objects → table
  if (Array.isArray(parsed) && parsed.length > 0 && typeof parsed[0] === 'object' && parsed[0] !== null) {
    const rows = parsed as Record<string, unknown>[];
    const columns = Object.keys(rows[0]);
    const markdown = arrayToMarkdownTable(columns, rows, 30);
    return {
      name: file.name,
      type: file.type || 'application/json',
      size: file.size,
      text: markdown,
      summary: 'JSON 文件',
      rows: rows.length,
      columns,
    };
  }

  // Any other JSON structure
  const pretty = JSON.stringify(parsed, null, 2);
  return {
    name: file.name,
    type: file.type || 'application/json',
    size: file.size,
    text: truncate(pretty, 5000),
    summary: 'JSON 文件',
  };
}

/**
 * Parse a plain-text file (TXT, MD, LOG, etc.).
 */
async function parseText(file: File): Promise<ParsedFile> {
  const text = await readFileAsText(file);
  const lines = text.split('\n').length;
  return {
    name: file.name,
    type: file.type || 'text/plain',
    size: file.size,
    text: truncate(text, 10000),
    summary: `文本文件，${lines} 行`,
  };
}

/**
 * Handle a PDF file.
 *
 * Full PDF text extraction in the browser is non-trivial without a heavy
 * dependency.  We return metadata so the caller can decide to use the AI
 * vision / document-analysis path instead.
 */
async function parsePdf(file: File): Promise<ParsedFile> {
  return {
    name: file.name,
    type: file.type || 'application/pdf',
    size: file.size,
    text: `[PDF 文件: ${file.name}]`,
    summary: `PDF 文件（${formatFileSize(file.size)}），内容需要 AI 视觉分析`,
  };
}

/**
 * Handle an image file.
 *
 * Images are not parsed for text; the caller typically encodes them as a
 * data-URL and sends them to a vision-capable model.
 */
async function parseImage(file: File): Promise<ParsedFile> {
  // Attempt to read width/height via an Image element
  const { width, height } = await new Promise<{ width: number; height: number }>((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      resolve({ width: img.naturalWidth, height: img.naturalHeight });
      URL.revokeObjectURL(url);
    };
    img.onerror = () => {
      resolve({ width: 0, height: 0 });
      URL.revokeObjectURL(url);
    };
    img.src = url;
  });

  return {
    name: file.name,
    type: file.type,
    size: file.size,
    text: '',
    summary:
      width > 0 && height > 0
        ? `图片文件（${width}×${height}）`
        : `图片文件（${formatFileSize(file.size)}）`,
  };
}

// ---------------------------------------------------------------------------
// MIME / extension classification
// ---------------------------------------------------------------------------

type FileCategory =
  | 'csv'
  | 'excel'
  | 'json'
  | 'text'
  | 'pdf'
  | 'image'
  | 'unsupported';

function categorize(file: File): FileCategory {
  const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
  const mime = file.type.toLowerCase();

  // CSV / TSV
  if (ext === 'csv' || mime === 'text/csv' || ext === 'tsv' || mime === 'text/tab-separated-values') {
    return 'csv';
  }

  // Excel
  if (
    ext === 'xlsx' ||
    ext === 'xls' ||
    mime === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' ||
    mime === 'application/vnd.ms-excel'
  ) {
    return 'excel';
  }

  // JSON
  if (ext === 'json' || mime === 'application/json') {
    return 'json';
  }

  // PDF
  if (ext === 'pdf' || mime === 'application/pdf') {
    return 'pdf';
  }

  // Images
  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico'].includes(ext)) {
    return 'image';
  }

  // Plain text / markdown / log
  if (
    mime.startsWith('text/') ||
    ['txt', 'md', 'markdown', 'log', 'rtf', 'xml', 'yaml', 'yml', 'toml', 'ini', 'conf', 'sh', 'bash', 'zsh', 'bat'].includes(ext)
  ) {
    return 'text';
  }

  return 'unsupported';
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Parse a file and extract its text content.
 *
 * Supported formats: CSV, TSV, XLSX, XLS, JSON, TXT, MD, LOG, PDF, and images.
 */
export async function parseFile(file: File): Promise<ParsedFile> {
  const category = categorize(file);

  switch (category) {
    case 'csv':
      return parseCsv(file);
    case 'excel':
      return parseExcel(file);
    case 'json':
      return parseJson(file);
    case 'text':
      return parseText(file);
    case 'pdf':
      return parsePdf(file);
    case 'image':
      return parseImage(file);
    default:
      return {
        name: file.name,
        type: file.type,
        size: file.size,
        text: '',
        summary: '不支持的文件格式',
      };
  }
}
