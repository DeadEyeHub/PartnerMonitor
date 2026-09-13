// Run with the configured bundled workspace Node runtime; no provider requests.
import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const [input, output, previewDir] = process.argv.slice(2);
if (!input || !output) throw new Error('Usage: build-workbook.mjs input.workbook.json output.xlsx [preview-directory]');
const data = JSON.parse(await fs.readFile(input, 'utf8'));
const wb = Workbook.create();
const expected = ['Overview', 'Findings', 'Financials', 'Sanctions', 'Changes', 'Data Quality'];
if (JSON.stringify(Object.keys(data.sheets)) !== JSON.stringify(expected)) throw new Error('Unexpected report sheets');
const safe = value => typeof value === 'string' && /^[\s]*[=+@-]/.test(value) ? "'" + value : value;
for (const name of expected) {
  const sheet = wb.worksheets.add(name);
  sheet.showGridLines = false;
  if (name === 'Overview') sheet.tabColor = '#183b56';
  const headers = data.headers[name], rows = data.sheets[name];
  const end = Math.max(6, rows.length + 5);
  const all = sheet.getRangeByIndexes(0, 0, end, headers.length);
  all.format.font = { name: 'Arial', size: 10, color: '#172435' };
  all.format.verticalAlignment = 'center';
  all.format.rowHeight = 24;
  all.format.columnWidth = 23;
  sheet.getRange('A2').values = [[name]];
  sheet.getRange('A2').format.font = { name: 'Arial', size: 14, bold: true };
  sheet.getRange('A3').values = [[name === 'Financials' ? 'Amounts in EUR units. Source statement IDs and ratios accompany each row.' :
    name === 'Changes' ? (data.previous_id ? 'Compared with assessment ' + data.previous_id : 'First baseline; no earlier assessment to compare.') :
    'Assessment ' + data.id + ' dated ' + data.created_at.slice(0, 10)]];
  sheet.getRange('A3').format.font = { name: 'Arial', size: 10, italic: true, color: '#596574' };
  const head = sheet.getRangeByIndexes(4, 0, 1, headers.length);
  head.values = [headers];
  head.format = { fill: '#183b56', font: { name: 'Arial', size: 10, bold: true, color: '#ffffff' },
    horizontalAlignment: 'center', wrapText: true, rowHeight: 32 };
  if (rows.length) {
    sheet.getRangeByIndexes(5, 0, rows.length, headers.length).values = rows.map(row => headers.map(h => {
      const value = row[h] ?? null;
      if (h === 'Date' && typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) return new Date(value + 'T00:00:00Z');
      return safe(value);
    }));
    for (let i = 0; i < headers.length; i++) {
      const column = sheet.getRangeByIndexes(5, i, rows.length, 1);
      const h = headers[i];
      if (['Reliability score','New findings','Year','Employees'].includes(h)) column.setNumberFormat('#,##0');
      if (['Revenue','Profit','Equity','Assets'].includes(h)) column.setNumberFormat('#,##0.00');
      if (h === 'Coverage') column.setNumberFormat('0.0"%"');
      if (h === 'Date') column.setNumberFormat('yyyy-mm-dd');
      if (h === 'Registration number') column.setNumberFormat('@');
      if (h === 'Year') column.setNumberFormat('0');
      if (['Company','Checked entity','Entity type','Match status','Status'].includes(h)) {
        column.format.wrapText = true;
        column.format.verticalAlignment = 'top';
      }
      if (['Finding','Main reason','Evidence','Ratios','Reason','Recommended action','Field','Previous value','Current value','Source'].includes(h)) {
        column.format.columnWidth = h === 'Main reason' || h === 'Finding' ? 65 : 45;
        column.format.wrapText = true;
        column.format.verticalAlignment = 'top';
      }
    }
    sheet.getRangeByIndexes(5,0,rows.length,1).format.columnWidth = 33;
    sheet.getRangeByIndexes(5,0,rows.length,headers.length).format.autofitRows();
  } else sheet.getRange('A6').values = [['No records for this assessment.']];
  sheet.freezePanes.freezeRows(5);
}
wb.recalculate();
console.log((await wb.inspect({kind:'table',range:'Overview!A5:H8',maxChars:2500,tableMaxCellChars:120})).ndjson);
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!',options:{useRegex:true,maxResults:20},maxChars:1000})).ndjson);
if (previewDir) {
  await fs.mkdir(previewDir,{recursive:true});
  for (const sheetName of expected) {
    const preview = await wb.render({sheetName,range:'A1:H8',scale:1,format:'png'});
    await fs.writeFile(path.join(previewDir,sheetName.replaceAll(' ','-')+'.png'),new Uint8Array(await preview.arrayBuffer()));
  }
}
await fs.mkdir(path.dirname(output), {recursive:true});
const staged = output + '.tmp.xlsx';
await (await SpreadsheetFile.exportXlsx(wb)).save(staged);
await fs.rename(staged,output);
console.log(JSON.stringify({workbook:output,assessment_id:data.id,sheets:expected.map(name=>({name,rows:data.sheets[name].length}))}));
