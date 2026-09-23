const SHEET_NAME = 'Ежедневные отчёты';
const HEADERS = [
  'Дата',
  'Начало смены',
  'Конец смены',
  'Источник смены',
  'Обращения',
  'С ответом',
  'В SLA',
  'SLA %',
  'Норматив SLA, мин',
  'Медиана ответа, мин',
  'FAQ Coverage %',
  'Без ответа',
  'Нарушения SLA',
  'Ответ позже SLA',
  'Reply-ответы поддержки',
  'Связанные ответы',
  'Ответы без исходного',
  'Корневые причины',
  'Исключено: диалоги партнёров',
  'Исключено: не-обращения',
  'Исключено: автоматика',
  'Комментарий',
  'Telegram chat ID',
  'Обновлено',
];

function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents || '{}');
    const expectedSecret = PropertiesService.getScriptProperties().getProperty('WEBHOOK_SECRET');
    if (!expectedSecret || payload.secret !== expectedSecret) {
      return jsonResponse({ ok: false, error: 'unauthorized' });
    }

    const report = payload.report || {};
    if (!/^\d{4}-\d{2}-\d{2}$/.test(report.date || '')) {
      return jsonResponse({ ok: false, error: 'invalid date' });
    }

    const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = spreadsheet.getSheetByName(SHEET_NAME) || spreadsheet.insertSheet(SHEET_NAME);
    ensureHeader(sheet);

    const causes = (report.rootCauses || [])
      .map(item => `${item.label}: ${item.count} (${round(item.percent)}%)`)
      .join('; ');
    const excluded = report.excluded || {};
    const values = [
      report.date,
      report.shiftStart,
      report.shiftEnd,
      report.shiftSource,
      report.tickets,
      report.responded,
      report.slaCompliant,
      ratio(report.slaPercent),
      report.slaTargetMinutes,
      report.medianResponseMinutes,
      ratio(report.faqCoveragePercent),
      report.unanswered,
      report.slaBreaches,
      report.delayedReplies,
      report.supportReplyMessages,
      report.linkedSupportReplies,
      report.unlinkedReplyTargets,
      causes,
      excluded.partner_dialogue || 0,
      excluded.non_inquiry || 0,
      excluded.automated || 0,
      report.commentary || '',
      String(report.reportChatId || ''),
      new Date(),
    ];

    const targetRow = findDateRow(sheet, report.date) || sheet.getLastRow() + 1;
    sheet.getRange(targetRow, 1, 1, values.length).setValues([values]);
    formatSheet(sheet);
    return jsonResponse({ ok: true, row: targetRow, date: report.date });
  } catch (error) {
    return jsonResponse({ ok: false, error: String(error) });
  }
}

function ensureHeader(sheet) {
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
    sheet.setFrozenRows(1);
  }
}

function findDateRow(sheet, dateValue) {
  if (sheet.getLastRow() < 2) return null;
  const dates = sheet.getRange(2, 1, sheet.getLastRow() - 1, 1).getDisplayValues();
  const index = dates.findIndex(row => row[0] === dateValue);
  return index < 0 ? null : index + 2;
}

function formatSheet(sheet) {
  const lastRow = sheet.getLastRow();
  sheet.getRange(1, 1, 1, HEADERS.length)
    .setBackground('#6D28D9')
    .setFontColor('#FFFFFF')
    .setFontWeight('bold')
    .setVerticalAlignment('middle')
    .setWrap(true);
  if (lastRow > 1) {
    sheet.getRange(2, 8, lastRow - 1, 1).setNumberFormat('0.0%');
    sheet.getRange(2, 11, lastRow - 1, 1).setNumberFormat('0.0%');
    sheet.getRange(2, 24, lastRow - 1, 1).setNumberFormat('dd.mm.yyyy hh:mm:ss');
  }
  sheet.autoResizeColumns(1, HEADERS.length);
  sheet.setColumnWidth(18, 280);
  sheet.setColumnWidth(22, 380);
}

function ratio(value) {
  return value == null ? '' : Number(value) / 100;
}

function round(value) {
  return Math.round(Number(value || 0) * 10) / 10;
}

function jsonResponse(value) {
  return ContentService
    .createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}
