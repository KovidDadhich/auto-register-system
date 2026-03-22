/**
 * Case_History_Fetch — Google Apps Script
 *
 * Purpose: When user types a Case ID in cell B1 of Case_History_Fetch sheet,
 * this script fetches all hearing history for that case from Master Sheet
 * and displays it in a vertical table starting at row 6.
 *
 * Layout populated by this script:
 *   A1: "Case ID:"   B1: [case ID input — typed by user]
 *   A3: Static column headers (written by setup_sheet.py)
 *   A4: Static column values ← written here
 *   A5: Dynamic column headers (written by setup_sheet.py)
 *   A6 onwards: One row per hearing date, newest first ← written here
 *
 * Installation:
 *   1. Open Google Sheet in browser
 *   2. Extensions → Apps Script
 *   3. Delete existing code, paste this entire file
 *   4. Save (Ctrl+S)
 *   5. Close Apps Script editor
 */

// ── Constants ──────────────────────────────────────────────────────────────────
var MASTER_SHEET_NAME    = "Master Sheet";
var HISTORY_SHEET_NAME   = "Case_History_Fetch";
var STATIC_COLS          = 11;   // A-K in Master Sheet (S.No. + 10 case fields)
var DYNAMIC_COLS         = 8;    // per date group
var MASTER_DATA_START    = 3;    // first data row in Master Sheet
var HISTORY_DATA_ROW     = 4;    // row for static values in history sheet
var HISTORY_DYNAMIC_ROW  = 6;    // first row for hearing date rows


// ── Trigger: fires on any cell edit ───────────────────────────────────────────
function onEdit(e) {
  var sheet = e.source.getActiveSheet();

  // Only trigger on Case_History_Fetch sheet, cell B1
  if (sheet.getName() !== HISTORY_SHEET_NAME) return;
  if (e.range.getA1Notation() !== "B1") return;

  var caseId = e.range.getValue().toString().trim();
  if (!caseId) {
    _clearHistoryData(sheet);
    return;
  }

  fetchCaseHistory(sheet, caseId);
}


// ── Main function ──────────────────────────────────────────────────────────────
function fetchCaseHistory(historySheet, caseId) {
  var ss          = SpreadsheetApp.getActiveSpreadsheet();
  var masterSheet = ss.getSheetByName(MASTER_SHEET_NAME);

  if (!masterSheet) {
    historySheet.getRange("A4").setValue("Error: Master Sheet not found.");
    return;
  }

  // ── Get Master Sheet data ─────────────────────────────────────────────────
  var masterLastCol = masterSheet.getLastColumn();
  var masterLastRow = masterSheet.getLastRow();

  if (masterLastRow < MASTER_DATA_START) {
    historySheet.getRange("A4").setValue("Master Sheet has no data.");
    return;
  }

  // Read row 1 (date headers) and all data rows
  var row1      = masterSheet.getRange(1, 1, 1, masterLastCol).getValues()[0];
  var dataRange = masterSheet.getRange(MASTER_DATA_START, 1, masterLastRow - MASTER_DATA_START + 1, masterLastCol);
  var allData   = dataRange.getValues();

  // ── Find the case row ─────────────────────────────────────────────────────
  // Case ID is in column B (index 1)
  var caseRowIdx = -1;
  for (var i = 0; i < allData.length; i++) {
    if (allData[i][1].toString().trim() === caseId) {
      caseRowIdx = i;
      break;
    }
  }

  if (caseRowIdx === -1) {
    _clearHistoryData(historySheet);
    historySheet.getRange("A4").setValue("Case ID not found: " + caseId);
    return;
  }

  var caseRow = allData[caseRowIdx];

  // ── Write static values in row 4 ─────────────────────────────────────────
  // Static cols in Master: A=S.No., B=Case ID, C=Case Name, D=District,
  // E=Tehsil, F=Old Case ID, G=Connected Prakaran, H=Prakaran,
  // I=Adhiniyam, J=To Be Continued?, K=Client In Contact?
  // Indices (0-based): 1,2,3,4,5,6,7,8,9,10
  var staticValues = [
    caseRow[1],   // Case ID
    caseRow[2],   // Case Name
    caseRow[3],   // District
    caseRow[4],   // Tehsil
    caseRow[5],   // Old Case ID
    caseRow[6],   // Connected Prakaran
    caseRow[7],   // Prakaran
    caseRow[8],   // Adhiniyam
    caseRow[9],   // To Be Continued?
    caseRow[10],  // Client In Contact?
  ];
  historySheet.getRange(HISTORY_DATA_ROW, 1, 1, staticValues.length).setValues([staticValues]);

  // ── Find all date column groups in Master Sheet ───────────────────────────
  // Date groups start at col index STATIC_COLS (0-based), step DYNAMIC_COLS
  var hearingRows = [];

  for (var colIdx = STATIC_COLS; colIdx < masterLastCol; colIdx += DYNAMIC_COLS) {
    // Check if this column has a date header in row 1
    var headerCell = row1[colIdx];
    if (!headerCell || headerCell.toString().trim() === "") continue;

    // Current Hearing Date is offset +1 from group start (2nd dynamic col)
    var currentHearingDate = caseRow[colIdx + 1];
    if (!currentHearingDate || currentHearingDate.toString().trim() === "") continue;

    // Extract date label from header "DD/MM/YYYY DayName"
    var dateLabel = headerCell.toString().trim().split(" ")[0];

    // Collect all 8 dynamic columns for this date
    var dynamicValues = [
      dateLabel,              // Hearing Date (from header)
      caseRow[colIdx],        // Prev Hearing Date
      caseRow[colIdx + 1],    // Current Hearing Date
      caseRow[colIdx + 2],    // Bench Name
      caseRow[colIdx + 3],    // Bench Number
      caseRow[colIdx + 4],    // Bench Member
      caseRow[colIdx + 5],    // Status
      caseRow[colIdx + 6],    // Comments
      caseRow[colIdx + 7],    // Next Hearing Date
    ];

    hearingRows.push({
      dateStr : _parseDateStr(dateLabel),   // YYYY-MM-DD for sorting
      values  : dynamicValues,
    });
  }

  // ── Sort newest first ──────────────────────────────────────────────────────
  hearingRows.sort(function(a, b) {
    return b.dateStr.localeCompare(a.dateStr);
  });

  // ── Clear old data and write new ──────────────────────────────────────────
  _clearHistoryData(historySheet);

  if (hearingRows.length === 0) {
    historySheet.getRange("A" + HISTORY_DYNAMIC_ROW).setValue("No hearing history found.");
    return;
  }

  // Write static values
  historySheet.getRange(HISTORY_DATA_ROW, 1, 1, staticValues.length).setValues([staticValues]);

  // Write hearing rows starting at row 6
  var rowData = hearingRows.map(function(r) { return r.values; });
  historySheet.getRange(HISTORY_DYNAMIC_ROW, 1, rowData.length, rowData[0].length).setValues(rowData);

  Logger.log("Case history loaded for case_id=" + caseId + " | " + hearingRows.length + " hearing(s) found.");
}


// ── Helpers ───────────────────────────────────────────────────────────────────

function _clearHistoryData(sheet) {
  /**
   * Clears rows 4 and 6 onwards, leaving headers (rows 3 and 5) intact.
   */
  var lastRow = sheet.getLastRow();

  // Clear static values row 4
  sheet.getRange("A4:Z4").clearContent();

  // Clear hearing rows from row 6 onwards
  if (lastRow >= HISTORY_DYNAMIC_ROW) {
    sheet.getRange(HISTORY_DYNAMIC_ROW, 1, lastRow - HISTORY_DYNAMIC_ROW + 1, 26).clearContent();
  }
}


function _parseDateStr(ddmmyyyy) {
  /**
   * Converts "DD/MM/YYYY" → "YYYY-MM-DD" for string-based sorting.
   */
  try {
    var parts = ddmmyyyy.split("/");
    return parts[2] + "-" + parts[1] + "-" + parts[0];
  } catch(e) {
    return "0000-00-00";
  }
}