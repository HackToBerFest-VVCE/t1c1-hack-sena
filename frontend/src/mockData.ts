// ── ClaimLens AI – mock data ───────────────────────────────────────────────────
// 6 claims: 2 High, 2 Medium, 2 Low risk

import type { Claim, AuditEntry } from './types'

// Placeholder page images (public domain placeholder service)
const PAGE = (w = 794, h = 1123, label = 'Page') =>
  `https://placehold.co/${w}x${h}/e8f0fe/1a56db?text=${encodeURIComponent(label)}`

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
      id:            'DOC-h1-bill',
      filename:      'fortis_bill_menon.pdf',
      document_type: 'hospital_bill',
      page_count:    2,
      page_image_urls: [PAGE(794, 1123, 'Bill p.1'), PAGE(794, 1123, 'Bill p.2')],
      fields: [
        { id: 'f1', name: 'Patient Name',    value: 'Ravi Shankar Menon',   confidence: 0.97, page_number: 1 },
        { id: 'f2', name: 'Hospital Name',   value: 'Fortis Hospital',      confidence: 0.98, page_number: 1 },
        { id: 'f3', name: 'Bill Number',     value: 'FH/2024/003311',       confidence: 0.99, page_number: 1 },
        { id: 'f4', name: 'Bill Date',       value: '28-Oct-2024',          confidence: 0.96, page_number: 1 },
        { id: 'f5', name: 'Admission Date',  value: '22-Oct-2024',          confidence: 0.95, page_number: 1 },
        { id: 'f6', name: 'Discharge Date',  value: '27-Oct-2024',          confidence: 0.95, page_number: 1 },
        { id: 'f7', name: 'Total Amount',    value: 'Rs. 1,24,500.00',      confidence: 0.98, page_number: 2 },
      ],
    },
    {
      id:            'DOC-h1-form',
      filename:      'claim_form_menon.pdf',
      document_type: 'claim_form',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Claim Form')],
      fields: [
        { id: 'f8',  name: 'Policy Number',   value: 'HDFC-HI-2024-00341', confidence: 0.99, page_number: 1 },
        { id: 'f9',  name: 'Claimed Amount',  value: 'Rs. 1,24,500.00',    confidence: 0.97, page_number: 1 },
        { id: 'f10', name: 'Claimant Signature', value: 'Present',         confidence: 0.91, page_number: 1 },
      ],
    },
  ],
  findings: [
    {
      id:               'FND-h1-01',
      rule_id:          'RULE-DUPLICATE-BILL',
      severity:         'high',
      what:             'Duplicate bill number matches CLM-2024-0028',
      evidence: [
        { document_filename: 'fortis_bill_menon.pdf', page_number: 1 },
      ],
      why_it_matters:   'The same bill FH/2024/003311 was already reimbursed in full under claim CLM-2024-0028 on 01-Nov-2024.',
      suggested_action: 'Reject claim and notify insured of duplicate submission policy.',
    },
    {
      id:               'FND-h1-02',
      rule_id:          'RULE-POLICY-LIMIT',
      severity:         'medium',
      what:             'Policy sum-insured 92 % exhausted',
      evidence: [
        { document_filename: 'claim_form_menon.pdf', page_number: 1 },
      ],
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
      id:            'DOC-h2-bill',
      filename:      'max_bill_agarwal.pdf',
      document_type: 'hospital_bill',
      page_count:    2,
      page_image_urls: [PAGE(794, 1123, 'Bill p.1'), PAGE(794, 1123, 'Bill p.2')],
      fields: [
        { id: 'f11', name: 'Patient Name',   value: 'Sunita Agarwal',        confidence: 0.96, page_number: 1 },
        { id: 'f12', name: 'Hospital Name',  value: 'Max Super Speciality',  confidence: 0.97, page_number: 1 },
        { id: 'f13', name: 'Bill Number',    value: 'MAX/2024/009847',        confidence: 0.99, page_number: 1 },
        { id: 'f14', name: 'Admission Date', value: '01-Nov-2024',            confidence: 0.95, page_number: 1 },
        { id: 'f15', name: 'Discharge Date', value: '05-Nov-2024',            confidence: 0.95, page_number: 1 },
        { id: 'f16', name: 'Total Amount',   value: 'Rs. 1,68,320.00',        confidence: 0.98, page_number: 2 },
      ],
    },
    {
      id:            'DOC-h2-form',
      filename:      'claim_form_agarwal.pdf',
      document_type: 'claim_form',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Claim Form')],
      fields: [
        { id: 'f17', name: 'Policy Number',  value: 'ICICI-LOM-2024-11203',  confidence: 0.99, page_number: 1 },
        { id: 'f18', name: 'Claimed Amount', value: 'Rs. 2,15,000.00',        confidence: 0.97, page_number: 1 },
      ],
    },
    {
      id:            'DOC-h2-discharge',
      filename:      'discharge_agarwal.pdf',
      document_type: 'discharge_summary',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Discharge Summary')],
      fields: [
        { id: 'f19', name: 'Diagnosis',      value: 'Viral Fever',            confidence: 0.88, page_number: 1 },
        { id: 'f20', name: 'Procedure',      value: 'Conservative management', confidence: 0.85, page_number: 1 },
      ],
    },
  ],
  findings: [
    {
      id:               'FND-h2-01',
      rule_id:          'RULE-AMOUNT-MISMATCH',
      severity:         'high',
      what:             'Claimed amount exceeds billed total by Rs. 46,680',
      evidence: [
        { document_filename: 'max_bill_agarwal.pdf',   page_number: 2 },
        { document_filename: 'claim_form_agarwal.pdf', page_number: 1 },
      ],
      why_it_matters:   'Insured is claiming Rs. 2,15,000 against a verified bill of Rs. 1,68,320. The Rs. 46,680 overage has no supporting document.',
      suggested_action: 'Query insured for additional invoices or reduce reimbursement to Rs. 1,68,320.',
    },
    {
      id:               'FND-h2-02',
      rule_id:          'RULE-DIAGNOSIS-PROCEDURE',
      severity:         'medium',
      what:             'Discharge diagnosis (Viral Fever) inconsistent with OT charges in bill',
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
      id:            'DOC-m1-bill',
      filename:      'manipal_bill_iyer.pdf',
      document_type: 'hospital_bill',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Bill p.1')],
      fields: [
        { id: 'f21', name: 'Patient Name',   value: 'Priya Venkataraman Iyer', confidence: 0.97, page_number: 1 },
        { id: 'f22', name: 'Bill Date',      value: '22-Oct-2024',              confidence: 0.96, page_number: 1 },
        { id: 'f23', name: 'Admission Date', value: '18-Oct-2024',              confidence: 0.95, page_number: 1 },
        { id: 'f24', name: 'Discharge Date', value: '21-Oct-2024',              confidence: 0.95, page_number: 1 },
        { id: 'f25', name: 'Total Amount',   value: 'Rs. 45,330.75',            confidence: 0.98, page_number: 1 },
      ],
    },
    {
      id:            'DOC-m1-form',
      filename:      'claim_form_iyer.pdf',
      document_type: 'claim_form',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Claim Form')],
      fields: [
        { id: 'f26', name: 'Policy Number',      value: 'ICICI-LOM-2023-88210', confidence: 0.99, page_number: 1 },
        { id: 'f27', name: 'Claimed Amount',     value: 'Rs. 45,330.75',         confidence: 0.97, page_number: 1 },
        { id: 'f28', name: 'Claimant Signature', value: 'Missing',               confidence: 0.93, page_number: 1 },
      ],
    },
    {
      id:            'DOC-m1-rx',
      filename:      'prescription_iyer.pdf',
      document_type: 'prescription',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Prescription')],
      fields: [
        { id: 'f29', name: 'Prescription Date', value: '24-Oct-2024', confidence: 0.90, page_number: 1 },
        { id: 'f30', name: 'Discharge Date',    value: '21-Oct-2024', confidence: 0.88, page_number: 1 },
      ],
    },
  ],
  findings: [
    {
      id:               'FND-m1-01',
      rule_id:          'RULE-MISSING-SIGNATURE',
      severity:         'medium',
      what:             'Claimant signature absent on claim form',
      evidence: [{ document_filename: 'claim_form_iyer.pdf', page_number: 1 }],
      why_it_matters:   'An unsigned claim form is not legally valid and cannot be processed per IRDAI guidelines.',
      suggested_action: 'Return claim form to insured for signature and resubmission.',
    },
    {
      id:               'FND-m1-02',
      rule_id:          'RULE-RX-DATE',
      severity:         'low',
      what:             'Prescription dated 3 days after discharge',
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
      id:            'DOC-m2-bill',
      filename:      'apollo_bill_pillai.pdf',
      document_type: 'hospital_bill',
      page_count:    2,
      page_image_urls: [PAGE(794, 1123, 'Bill p.1'), PAGE(794, 1123, 'Bill p.2')],
      fields: [
        { id: 'f31', name: 'Patient Name',   value: 'Arun K. Pillai',       confidence: 0.94, page_number: 1 },
        { id: 'f32', name: 'Bill Date',      value: '08-Nov-2024',           confidence: 0.97, page_number: 1 },
        { id: 'f33', name: 'Admission Date', value: '04-Nov-2024',           confidence: 0.95, page_number: 1 },
        { id: 'f34', name: 'Discharge Date', value: '07-Nov-2024',           confidence: 0.95, page_number: 1 },
        { id: 'f35', name: 'Room Type',      value: 'Deluxe Room',           confidence: 0.91, page_number: 1 },
        { id: 'f36', name: 'Total Amount',   value: 'Rs. 88,750.00',         confidence: 0.98, page_number: 2 },
      ],
    },
    {
      id:            'DOC-m2-form',
      filename:      'claim_form_pillai.pdf',
      document_type: 'claim_form',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Claim Form')],
      fields: [
        { id: 'f37', name: 'Policy Number',  value: 'STAR-HI-2024-33901',   confidence: 0.99, page_number: 1 },
        { id: 'f38', name: 'Insured Name',   value: 'Arun Kumar Pillai',    confidence: 0.99, page_number: 1 },
        { id: 'f39', name: 'Claimed Amount', value: 'Rs. 88,750.00',         confidence: 0.97, page_number: 1 },
        { id: 'f40', name: 'Room Entitlement', value: 'Standard Room',      confidence: 0.98, page_number: 1 },
      ],
    },
  ],
  findings: [
    {
      id:               'FND-m2-01',
      rule_id:          'RULE-NAME-MISMATCH',
      severity:         'medium',
      what:             'Name on bill differs from policy holder name',
      evidence: [
        { document_filename: 'apollo_bill_pillai.pdf',  page_number: 1 },
        { document_filename: 'claim_form_pillai.pdf', page_number: 1 },
      ],
      why_it_matters:   '"Arun K. Pillai" (bill) vs "Arun Kumar Pillai" (policy). Could be a data-entry variation but must be confirmed.',
      suggested_action: 'Request government ID copy to confirm identity before processing.',
    },
    {
      id:               'FND-m2-02',
      rule_id:          'RULE-ROOM-CATEGORY',
      severity:         'medium',
      what:             'Deluxe room charged; policy entitles Standard room only',
      evidence: [
        { document_filename: 'apollo_bill_pillai.pdf',  page_number: 1 },
        { document_filename: 'claim_form_pillai.pdf', page_number: 1 },
      ],
      why_it_matters:   'Excess room rent (difference between Deluxe and Standard rates) and proportionate deductions apply to all associated charges.',
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
  risk_reasons: [
    'Minor confidence gap on bill date field (0.81)',
  ],
  created_at: '2024-11-14T16:20:00Z',
  documents: [
    {
      id:            'DOC-l1-bill',
      filename:      'apollo_bill_sharma.pdf',
      document_type: 'hospital_bill',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Bill p.1')],
      fields: [
        { id: 'f41', name: 'Patient Name',   value: 'Rajesh Kumar Sharma',   confidence: 0.97, page_number: 1 },
        { id: 'f42', name: 'Policy Number',  value: 'HDFC-HI-2024-00341',   confidence: 0.99, page_number: 1 },
        { id: 'f43', name: 'Bill Date',      value: '14-Nov-2024',            confidence: 0.81, page_number: 1 },
        { id: 'f44', name: 'Admission Date', value: '08-Nov-2024',            confidence: 0.95, page_number: 1 },
        { id: 'f45', name: 'Discharge Date', value: '13-Nov-2024',            confidence: 0.95, page_number: 1 },
        { id: 'f46', name: 'Total Amount',   value: 'Rs. 98,450.50',          confidence: 0.98, page_number: 1 },
      ],
    },
    {
      id:            'DOC-l1-discharge',
      filename:      'discharge_sharma.pdf',
      document_type: 'discharge_summary',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Discharge Summary')],
      fields: [
        { id: 'f47', name: 'Diagnosis',  value: 'Acute Appendicitis – Laparoscopic Appendectomy', confidence: 0.96, page_number: 1 },
        { id: 'f48', name: 'Surgeon',    value: 'Dr. Mahesh Rao',                                  confidence: 0.94, page_number: 1 },
      ],
    },
  ],
  findings: [
    {
      id:               'FND-l1-01',
      rule_id:          'RULE-LOW-CONFIDENCE',
      severity:         'low',
      what:             'Bill date extracted with low confidence (0.81)',
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
  risk_reasons: [
    'All fields verified; routine diabetic-care claim',
  ],
  created_at: '2024-12-03T10:05:00Z',
  documents: [
    {
      id:            'DOC-l2-bill',
      filename:      'kda_bill_patel.pdf',
      document_type: 'hospital_bill',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Bill p.1')],
      fields: [
        { id: 'f49', name: 'Patient Name',   value: 'Suresh Hasmukhlal Patel', confidence: 0.98, page_number: 1 },
        { id: 'f50', name: 'Policy Number',  value: 'STAR-HI-2024-56789',      confidence: 0.99, page_number: 1 },
        { id: 'f51', name: 'Bill Date',      value: '03-Dec-2024',               confidence: 0.97, page_number: 1 },
        { id: 'f52', name: 'Admission Date', value: '27-Nov-2024',               confidence: 0.96, page_number: 1 },
        { id: 'f53', name: 'Discharge Date', value: '02-Dec-2024',               confidence: 0.96, page_number: 1 },
        { id: 'f54', name: 'Total Amount',   value: 'Rs. 74,320.00',             confidence: 0.98, page_number: 1 },
      ],
    },
    {
      id:            'DOC-l2-form',
      filename:      'claim_form_patel.pdf',
      document_type: 'claim_form',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Claim Form')],
      fields: [
        { id: 'f55', name: 'Claimed Amount',     value: 'Rs. 74,320.00', confidence: 0.97, page_number: 1 },
        { id: 'f56', name: 'Claimant Signature', value: 'Present',        confidence: 0.95, page_number: 1 },
      ],
    },
    {
      id:            'DOC-l2-discharge',
      filename:      'discharge_patel.pdf',
      document_type: 'discharge_summary',
      page_count:    1,
      page_image_urls: [PAGE(794, 1123, 'Discharge Summary')],
      fields: [
        { id: 'f57', name: 'Diagnosis',  value: 'Type-2 Diabetes Mellitus – Foot Ulcer', confidence: 0.96, page_number: 1 },
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
    id:        'AUD-001',
    user:      'analyst@claimlens.in',
    action:    'CLAIM_ESCALATED',
    claim_id:  'CLM-2024-0041',
    before:    { status: 'pending' },
    after:     { status: 'escalated' },
    timestamp: '2024-11-03T10:02:00Z',
  },
  {
    id:        'AUD-002',
    user:      'analyst@claimlens.in',
    action:    'CLAIM_QUERIED',
    claim_id:  'CLM-2024-0057',
    before:    { status: 'pending' },
    after:     { status: 'queried' },
    timestamp: '2024-11-07T15:11:00Z',
  },
  {
    id:        'AUD-003',
    user:      'reviewer@claimlens.in',
    action:    'FIELD_UPDATED',
    claim_id:  'CLM-2024-0063',
    before:    { field: 'Claimant Signature', value: 'Missing' },
    after:     { field: 'Claimant Signature', value: 'Pending resubmission' },
    timestamp: '2024-11-10T12:30:00Z',
  },
  {
    id:        'AUD-004',
    user:      'approver@claimlens.in',
    action:    'CLAIM_APPROVED',
    claim_id:  'CLM-2024-0079',
    before:    { status: 'pending' },
    after:     { status: 'approved' },
    timestamp: '2024-11-14T17:05:00Z',
  },
]
