// ── ClaimLens AI – mock data ───────────────────────────────────────────────────
// Used only when USE_MOCK = true in api.ts.
// ids are integers to match the backend's autoincrement PKs.
// Documents have no page_image_urls; the viewer builds URLs via getPageImageUrl.

import type { Claim, AuditEntry } from './types'

// ─────────────────────────────────────────────────────────────────────────────
// HIGH RISK #1 – Duplicate claim
// ─────────────────────────────────────────────────────────────────────────────
const claim_h1: Claim = {
  id:            'CLM-2024-0041',
  patient_name:  'Ravi Shankar Menon',
  hospital_name: 'Fortis Hospital, Bengaluru',
  status:        'escalated',
  risk_level:    'High',
  risk_score:    91,
  risk_reasons: [
    'Identical bill number submitted under a previous claim (CLM-2024-0028)',
    'Admission and discharge dates overlap with a processed claim',
    'Policy limit already 92 % exhausted this year',
  ],
  created_at: '2024-11-03T09:14:00Z',
  documents: [
    {
      id:            1,
      filename:      'fortis_bill_menon.pdf',
      document_type: 'hospital_bill',
      page_count:    2,
      fields: [
        { id: 1,  name: 'Patient Name',       value: 'Ravi Shankar Menon',   confidence: 0.97, page_number: 1 },
        { id: 2,  name: 'Hospital Name',      value: 'Fortis Hospital',      confidence: 0.98, page_number: 1 },
        { id: 3,  name: 'Bill Number',        value: 'FH/2024/003311',       confidence: 0.99, page_number: 1 },
        { id: 4,  name: 'Bill Date',          value: '28-Oct-2024',          confidence: 0.96, page_number: 1 },
        { id: 5,  name: 'Admission Date',     value: '22-Oct-2024',          confidence: 0.95, page_number: 1 },
        { id: 6,  name: 'Discharge Date',     value: '27-Oct-2024',          confidence: 0.95, page_number: 1 },
        { id: 7,  name: 'Total Amount',       value: 'Rs. 1,24,500.00',      confidence: 0.98, page_number: 2 },
      ],
    },
    {
      id:            2,
      filename:      'claim_form_menon.pdf',
      document_type: 'claim_form',
      page_count:    1,
      fields: [
        { id: 8,  name: 'Policy Number',      value: 'HDFC-HI-2024-00341',  confidence: 0.99, page_number: 1 },
        { id: 9,  name: 'Claimed Amount',     value: 'Rs. 1,24,500.00',     confidence: 0.97, page_number: 1 },
        { id: 10, name: 'Claimant Signature', value: 'Present',             confidence: 0.91, page_number: 1 },
      ],
    },
  ],
  findings: [
    {
      id: 1, rule_id: 'RULE-DUPLICATE-BILL', severity: 'high', weight: 40,
      what: 'Duplicate bill number matches CLM-2024-0028',
      evidence: [{ document_filename: 'fortis_bill_menon.pdf', page_number: 1 }],
      why_it_matters:   'The same bill FH/2024/003311 was already reimbursed in full under claim CLM-2024-0028 on 01-Nov-2024.',
      suggested_action: 'Reject claim and notify insured of duplicate submission policy.',
    },
    {
      id: 2, rule_id: 'RULE-POLICY-LIMIT', severity: 'medium', weight: 20,
      what: 'Policy sum-insured 92 % exhausted',
      evidence: [{ document_filename: 'claim_form_menon.pdf', page_number: 1 }],
      why_it_matters:   'Approving this claim would leave only Rs. 9,960 of the annual limit.',
      suggested_action: 'Verify remaining limit before any partial approval.',
    },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// HIGH RISK #2 – Claimed amount exceeds billed total
// ─────────────────────────────────────────────────────────────────────────────
const claim_h2: Claim = {
  id:            'CLM-2024-0057',
  patient_name:  'Sunita Devi Agarwal',
  hospital_name: 'Max Super Speciality Hospital, Delhi',
  status:        'queried',
  risk_level:    'High',
  risk_score:    85,
  risk_reasons: [
    'Claimed amount (Rs. 2,15,000) exceeds itemised bill total (Rs. 1,68,320)',
    'Discharge summary diagnosis does not match bill procedure codes',
  ],
  created_at: '2024-11-07T14:30:00Z',
  documents: [
    {
      id:            3,
      filename:      'max_bill_agarwal.pdf',
      document_type: 'hospital_bill',
      page_count:    2,
      fields: [
        { id: 11, name: 'Patient Name',   value: 'Sunita Agarwal',         confidence: 0.96, page_number: 1 },
        { id: 12, name: 'Hospital Name',  value: 'Max Super Speciality',   confidence: 0.97, page_number: 1 },
        { id: 13, name: 'Bill Number',    value: 'MAX/2024/009847',         confidence: 0.99, page_number: 1 },
        { id: 14, name: 'Admission Date', value: '01-Nov-2024',             confidence: 0.95, page_number: 1 },
        { id: 15, name: 'Discharge Date', value: '05-Nov-2024',             confidence: 0.95, page_number: 1 },
        { id: 16, name: 'Total Amount',   value: 'Rs. 1,68,320.00',         confidence: 0.98, page_number: 2 },
      ],
    },
    {
      id:            4,
      filename:      'claim_form_agarwal.pdf',
      document_type: 'claim_form',
      page_count:    1,
      fields: [
        { id: 17, name: 'Policy Number',  value: 'ICICI-LOM-2024-11203',   confidence: 0.99, page_number: 1 },
        { id: 18, name: 'Claimed Amount', value: 'Rs. 2,15,000.00',         confidence: 0.97, page_number: 1 },
      ],
    },
    {
      id:            5,
      filename:      'discharge_agarwal.pdf',
      document_type: 'discharge_summary',
      page_count:    1,
      fields: [
        { id: 19, name: 'Diagnosis', value: 'Viral Fever',              confidence: 0.88, page_number: 1 },
        { id: 20, name: 'Procedure', value: 'Conservative management',  confidence: 0.85, page_number: 1 },
      ],
    },
  ],
  findings: [
    {
      id: 3, rule_id: 'RULE-AMOUNT-MISMATCH', severity: 'high', weight: 35,
      what: 'Claimed amount exceeds billed total by Rs. 46,680',
      evidence: [
        { document_filename: 'max_bill_agarwal.pdf',   page_number: 2 },
        { document_filename: 'claim_form_agarwal.pdf', page_number: 1 },
      ],
      why_it_matters:   'Insured is claiming Rs. 2,15,000 against a verified bill of Rs. 1,68,320.',
      suggested_action: 'Query insured for additional invoices or reduce reimbursement.',
    },
    {
      id: 4, rule_id: 'RULE-DIAGNOSIS-PROCEDURE', severity: 'medium', weight: 20,
      what: 'Discharge diagnosis (Viral Fever) inconsistent with OT charges in bill',
      evidence: [
        { document_filename: 'discharge_agarwal.pdf', page_number: 1 },
        { document_filename: 'max_bill_agarwal.pdf',  page_number: 2 },
      ],
      why_it_matters:   'Operation Theatre charges of Rs. 32,000 appear on the bill but the discharge summary records no surgical procedure.',
      suggested_action: 'Request detailed OT notes and attending surgeon certificate.',
    },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// MEDIUM RISK #1 – Missing claimant signature
// ─────────────────────────────────────────────────────────────────────────────
const claim_m1: Claim = {
  id:            'CLM-2024-0063',
  patient_name:  'Priya Venkataraman Iyer',
  hospital_name: 'Manipal Hospital, Chennai',
  status:        'queried',
  risk_level:    'Medium',
  risk_score:    54,
  risk_reasons: [
    'Claim form claimant signature field is blank',
    'Prescription date is 3 days after discharge',
  ],
  created_at: '2024-11-10T11:00:00Z',
  documents: [
    {
      id:            6,
      filename:      'manipal_bill_iyer.pdf',
      document_type: 'hospital_bill',
      page_count:    1,
      fields: [
        { id: 21, name: 'Patient Name',   value: 'Priya Venkataraman Iyer', confidence: 0.97, page_number: 1 },
        { id: 22, name: 'Bill Date',      value: '22-Oct-2024',              confidence: 0.96, page_number: 1 },
        { id: 23, name: 'Admission Date', value: '18-Oct-2024',              confidence: 0.95, page_number: 1 },
        { id: 24, name: 'Discharge Date', value: '21-Oct-2024',              confidence: 0.95, page_number: 1 },
        { id: 25, name: 'Total Amount',   value: 'Rs. 45,330.75',            confidence: 0.98, page_number: 1 },
      ],
    },
    {
      id:            7,
      filename:      'claim_form_iyer.pdf',
      document_type: 'claim_form',
      page_count:    1,
      fields: [
        { id: 26, name: 'Policy Number',      value: 'ICICI-LOM-2023-88210', confidence: 0.99, page_number: 1 },
        { id: 27, name: 'Claimed Amount',     value: 'Rs. 45,330.75',         confidence: 0.97, page_number: 1 },
        { id: 28, name: 'Claimant Signature', value: 'Missing',               confidence: 0.93, page_number: 1 },
      ],
    },
    {
      id:            8,
      filename:      'prescription_iyer.pdf',
      document_type: 'prescription',
      page_count:    1,
      fields: [
        { id: 29, name: 'Prescription Date', value: '24-Oct-2024', confidence: 0.90, page_number: 1 },
        { id: 30, name: 'Discharge Date',    value: '21-Oct-2024', confidence: 0.88, page_number: 1 },
      ],
    },
  ],
  findings: [
    {
      id: 5, rule_id: 'RULE-MISSING-SIGNATURE', severity: 'medium', weight: 25,
      what: 'Claimant signature absent on claim form',
      evidence: [{ document_filename: 'claim_form_iyer.pdf', page_number: 1 }],
      why_it_matters:   'An unsigned claim form is not legally valid and cannot be processed per IRDAI guidelines.',
      suggested_action: 'Return claim form to insured for signature and resubmission.',
    },
    {
      id: 6, rule_id: 'RULE-RX-DATE', severity: 'low', weight: 10,
      what: 'Prescription dated 3 days after discharge',
      evidence: [{ document_filename: 'prescription_iyer.pdf', page_number: 1 }],
      why_it_matters:   'Post-discharge prescription may relate to outpatient care not covered under this hospitalisation policy.',
      suggested_action: 'Clarify with treating doctor whether prescription covers inpatient stay medicines.',
    },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// MEDIUM RISK #2 – Name mismatch between documents
// ─────────────────────────────────────────────────────────────────────────────
const claim_m2: Claim = {
  id:            'CLM-2024-0071',
  patient_name:  'Arun Kumar Pillai',
  hospital_name: 'Apollo Hospitals, Hyderabad',
  status:        'pending',
  risk_level:    'Medium',
  risk_score:    47,
  risk_reasons: [
    'Patient name on bill ("Arun K. Pillai") differs from policy name ("Arun Kumar Pillai")',
    'Room category charged (Deluxe) exceeds entitlement (Standard)',
  ],
  created_at: '2024-11-12T08:45:00Z',
  documents: [
    {
      id:            9,
      filename:      'apollo_bill_pillai.pdf',
      document_type: 'hospital_bill',
      page_count:    2,
      fields: [
        { id: 31, name: 'Patient Name',     value: 'Arun K. Pillai',      confidence: 0.94, page_number: 1 },
        { id: 32, name: 'Bill Date',        value: '08-Nov-2024',          confidence: 0.97, page_number: 1 },
        { id: 33, name: 'Admission Date',   value: '04-Nov-2024',          confidence: 0.95, page_number: 1 },
        { id: 34, name: 'Discharge Date',   value: '07-Nov-2024',          confidence: 0.95, page_number: 1 },
        { id: 35, name: 'Room Type',        value: 'Deluxe Room',          confidence: 0.91, page_number: 1 },
        { id: 36, name: 'Total Amount',     value: 'Rs. 88,750.00',         confidence: 0.98, page_number: 2 },
      ],
    },
    {
      id:            10,
      filename:      'claim_form_pillai.pdf',
      document_type: 'claim_form',
      page_count:    1,
      fields: [
        { id: 37, name: 'Policy Number',    value: 'STAR-HI-2024-33901',   confidence: 0.99, page_number: 1 },
        { id: 38, name: 'Insured Name',     value: 'Arun Kumar Pillai',    confidence: 0.99, page_number: 1 },
        { id: 39, name: 'Claimed Amount',   value: 'Rs. 88,750.00',         confidence: 0.97, page_number: 1 },
        { id: 40, name: 'Room Entitlement', value: 'Standard Room',        confidence: 0.98, page_number: 1 },
      ],
    },
  ],
  findings: [
    {
      id: 7, rule_id: 'RULE-NAME-MISMATCH', severity: 'medium', weight: 20,
      what: 'Name on bill differs from policy holder name',
      evidence: [
        { document_filename: 'apollo_bill_pillai.pdf',  page_number: 1 },
        { document_filename: 'claim_form_pillai.pdf', page_number: 1 },
      ],
      why_it_matters:   '"Arun K. Pillai" (bill) vs "Arun Kumar Pillai" (policy). Could be a data-entry variation but must be confirmed.',
      suggested_action: 'Request government ID copy to confirm identity before processing.',
    },
    {
      id: 8, rule_id: 'RULE-ROOM-CATEGORY', severity: 'medium', weight: 20,
      what: 'Deluxe room charged; policy entitles Standard room only',
      evidence: [
        { document_filename: 'apollo_bill_pillai.pdf',  page_number: 1 },
        { document_filename: 'claim_form_pillai.pdf', page_number: 1 },
      ],
      why_it_matters:   'Excess room rent and proportionate deductions apply to all associated charges.',
      suggested_action: 'Recalculate bill applying room-rent proportionate deduction clause.',
    },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// LOW RISK #1
// ─────────────────────────────────────────────────────────────────────────────
const claim_l1: Claim = {
  id:            'CLM-2024-0079',
  patient_name:  'Rajesh Kumar Sharma',
  hospital_name: 'Apollo Hospitals, Bengaluru',
  status:        'approved',
  risk_level:    'Low',
  risk_score:    18,
  risk_reasons:  ['Minor confidence gap on bill date field (0.81)'],
  created_at: '2024-11-14T16:20:00Z',
  documents: [
    {
      id:            11,
      filename:      'apollo_bill_sharma.pdf',
      document_type: 'hospital_bill',
      page_count:    1,
      fields: [
        { id: 41, name: 'Patient Name',   value: 'Rajesh Kumar Sharma',   confidence: 0.97, page_number: 1 },
        { id: 42, name: 'Policy Number',  value: 'HDFC-HI-2024-00341',   confidence: 0.99, page_number: 1 },
        { id: 43, name: 'Bill Date',      value: '14-Nov-2024',            confidence: 0.81, page_number: 1 },
        { id: 44, name: 'Admission Date', value: '08-Nov-2024',            confidence: 0.95, page_number: 1 },
        { id: 45, name: 'Discharge Date', value: '13-Nov-2024',            confidence: 0.95, page_number: 1 },
        { id: 46, name: 'Total Amount',   value: 'Rs. 98,450.50',          confidence: 0.98, page_number: 1 },
      ],
    },
    {
      id:            12,
      filename:      'discharge_sharma.pdf',
      document_type: 'discharge_summary',
      page_count:    1,
      fields: [
        { id: 47, name: 'Diagnosis', value: 'Acute Appendicitis – Laparoscopic Appendectomy', confidence: 0.96, page_number: 1 },
        { id: 48, name: 'Surgeon',   value: 'Dr. Mahesh Rao',                                  confidence: 0.94, page_number: 1 },
      ],
    },
  ],
  findings: [
    {
      id: 9, rule_id: 'RULE-LOW-CONFIDENCE', severity: 'low', weight: 10,
      what: 'Bill date extracted with low confidence (0.81)',
      evidence: [{ document_filename: 'apollo_bill_sharma.pdf', page_number: 1 }],
      why_it_matters:   'OCR confidence below 0.85 may indicate a smudged or handwritten date field.',
      suggested_action: 'Human reviewer to visually confirm bill date on original document.',
    },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// LOW RISK #2
// ─────────────────────────────────────────────────────────────────────────────
const claim_l2: Claim = {
  id:            'CLM-2024-0083',
  patient_name:  'Suresh Hasmukhlal Patel',
  hospital_name: 'Kokilaben Dhirubhai Ambani Hospital, Mumbai',
  status:        'pending',
  risk_level:    'Low',
  risk_score:    12,
  risk_reasons:  ['All fields verified; routine diabetic-care claim'],
  created_at: '2024-12-03T10:05:00Z',
  documents: [
    {
      id:            13,
      filename:      'kda_bill_patel.pdf',
      document_type: 'hospital_bill',
      page_count:    1,
      fields: [
        { id: 49, name: 'Patient Name',   value: 'Suresh Hasmukhlal Patel', confidence: 0.98, page_number: 1 },
        { id: 50, name: 'Policy Number',  value: 'STAR-HI-2024-56789',      confidence: 0.99, page_number: 1 },
        { id: 51, name: 'Bill Date',      value: '03-Dec-2024',               confidence: 0.97, page_number: 1 },
        { id: 52, name: 'Admission Date', value: '27-Nov-2024',               confidence: 0.96, page_number: 1 },
        { id: 53, name: 'Discharge Date', value: '02-Dec-2024',               confidence: 0.96, page_number: 1 },
        { id: 54, name: 'Total Amount',   value: 'Rs. 74,320.00',             confidence: 0.98, page_number: 1 },
      ],
    },
    {
      id:            14,
      filename:      'claim_form_patel.pdf',
      document_type: 'claim_form',
      page_count:    1,
      fields: [
        { id: 55, name: 'Claimed Amount',     value: 'Rs. 74,320.00', confidence: 0.97, page_number: 1 },
        { id: 56, name: 'Claimant Signature', value: 'Present',        confidence: 0.95, page_number: 1 },
      ],
    },
    {
      id:            15,
      filename:      'discharge_patel.pdf',
      document_type: 'discharge_summary',
      page_count:    1,
      fields: [
        { id: 57, name: 'Diagnosis', value: 'Type-2 Diabetes Mellitus – Foot Ulcer', confidence: 0.96, page_number: 1 },
      ],
    },
  ],
  findings: [],
}

// ── exports ───────────────────────────────────────────────────────────────────

export const MOCK_CLAIMS: Claim[] = [
  claim_h1,
  claim_h2,
  claim_m1,
  claim_m2,
  claim_l1,
  claim_l2,
]

export const MOCK_AUDIT: AuditEntry[] = [
  {
    id:        1,
    user:      'analyst@claimlens.in',
    action:    'CLAIM_ESCALATED',
    claim_id:  'CLM-2024-0041',
    before:    { status: 'pending' },
    after:     { status: 'escalated' },
    timestamp: '2024-11-03T10:02:00Z',
  },
  {
    id:        2,
    user:      'analyst@claimlens.in',
    action:    'CLAIM_QUERIED',
    claim_id:  'CLM-2024-0057',
    before:    { status: 'pending' },
    after:     { status: 'queried' },
    timestamp: '2024-11-07T15:11:00Z',
  },
  {
    id:        3,
    user:      'reviewer@claimlens.in',
    action:    'FIELD_UPDATED',
    claim_id:  'CLM-2024-0063',
    before:    { field: 'Claimant Signature', value: 'Missing' },
    after:     { field: 'Claimant Signature', value: 'Pending resubmission' },
    timestamp: '2024-11-10T12:30:00Z',
  },
  {
    id:        4,
    user:      'approver@claimlens.in',
    action:    'CLAIM_APPROVED',
    claim_id:  'CLM-2024-0079',
    before:    { status: 'pending' },
    after:     { status: 'approved' },
    timestamp: '2024-11-14T17:05:00Z',
  },
]
