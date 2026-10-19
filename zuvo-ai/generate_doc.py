import docx
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

def set_cell_background(cell, fill_hex):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>')
    tcPr.append(shd)

def set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = parse_xml(f'''
        <w:tcMar {nsdecls("w")}>
            <w:top w:w="{top}" w:type="dxa"/>
            <w:bottom w:w="{bottom}" w:type="dxa"/>
            <w:left w:w="{left}" w:type="dxa"/>
            <w:right w:w="{right}" w:type="dxa"/>
        </w:tcMar>
    ''')
    tcPr.append(tcMar)

def set_table_borders(table, color="CBD5E0", sz="4", val="single"):
    tblPr = table._tbl.tblPr
    borders = parse_xml(f'''
        <w:tblBorders {nsdecls("w")}>
            <w:top w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>
            <w:bottom w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>
            <w:insideH w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>
            <w:insideV w:val="none"/>
            <w:left w:val="none"/>
            <w:right w:val="none"/>
        </w:tblBorders>
    ''')
    tblPr.append(borders)

def add_callout(doc, text, title="STRATEGIC IMPERATIVE"):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Inches(6.5)
    cell = table.cell(0, 0)
    set_cell_background(cell, "F0F4F8")
    set_cell_margins(cell, top=140, bottom=140, left=200, right=160)
    
    tcPr = cell._tc.get_or_add_tcPr()
    borders = parse_xml(f'''
        <w:tcBorders {nsdecls("w")}>
            <w:left w:val="single" w:sz="24" w:space="0" w:color="1B365D"/>
            <w:top w:val="none"/>
            <w:right w:val="none"/>
            <w:bottom w:val="none"/>
        </w:tcBorders>
    ''')
    tcPr.append(borders)
    
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(4)
    run_t = p.add_run(f"[{title}] ")
    run_t.bold = True
    run_t.font.name = "Calibri"
    run_t.font.size = Pt(10.5)
    run_t.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
    
    run_txt = p.add_run(text)
    run_txt.font.name = "Calibri"
    run_txt.font.size = Pt(10)
    run_txt.font.italic = True
    run_txt.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)

def build_document():
    doc = Document()
    
    # Page Setup - Normal Margins (1 inch)
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)
        
        header = section.header
        hp = header.paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        hrun = hp.add_run("Zuvocloud AI Solution | Strategic Master Blueprint (IIM-A Edition)")
        hrun.font.name = "Calibri"
        hrun.font.size = Pt(8.5)
        hrun.font.color.rgb = RGBColor(0x71, 0x80, 0x96)
        
        footer = section.footer
        fp = footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        frun = fp.add_run("Confidential — ZuvoCloud Pvt. Ltd. — For Executive & Operational Use Only")
        frun.font.name = "Calibri"
        frun.font.size = Pt(8.5)
        frun.font.color.rgb = RGBColor(0xA0, 0xAE, 0xC0)

    # -------------------------------------------------------------
    # DOCUMENT TITLE (EXACT TOP REQUIREMENT: Zuvocloud AI Solution)
    # -------------------------------------------------------------
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = Pt(0)
    p_title.paragraph_format.space_after = Pt(2)
    p_title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    r_title = p_title.add_run("Zuvocloud AI Solution")
    r_title.bold = True
    r_title.font.name = "Arial"
    r_title.font.size = Pt(26)
    r_title.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p_sub = doc.add_paragraph()
    p_sub.paragraph_format.space_before = Pt(0)
    p_sub.paragraph_format.space_after = Pt(14)
    r_sub = p_sub.add_run("Multi-Tenant WhatsApp AI Business Operations Platform\nComprehensive Strategic PRD, Business Plan, Target Customer ICP, Pricing & Financial Model")
    r_sub.font.name = "Calibri"
    r_sub.font.size = Pt(13)
    r_sub.font.color.rgb = RGBColor(0x4A, 0x55, 0x68)

    # Meta Table Box
    meta_table = doc.add_table(rows=4, cols=2)
    meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta_table.autofit = False
    meta_table.columns[0].width = Inches(2.0)
    meta_table.columns[1].width = Inches(4.5)
    set_table_borders(meta_table, color="CBD5E0", sz="6")
    
    meta_data = [
        ("Venture Lead / Author:", "Senior Venture Strategist & Serial Entrepreneur (IIM Ahmedabad Alumnus, 30+ Yrs Exp.)"),
        ("Corporate Entity:", "ZuvoCloud Pvt. Ltd. (zuvocloud.com) | Founders: Jitendra (Intel Background) & Operations Team"),
        ("Target Market & Hub:", "Jaipur, Rajasthan (VKI, Sitapura, Mansarovar) → Pan-India B2B SME Distribution"),
        ("Document Status & Version:", "Executive Release v1.1 | Commercially Validated PRD, AI Guardrails & POC Acceptance Blueprint")
    ]
    
    for i, (k, v) in enumerate(meta_data):
        c0 = meta_table.cell(i, 0)
        c1 = meta_table.cell(i, 1)
        set_cell_background(c0, "F7FAFC")
        set_cell_background(c1, "FFFFFF")
        set_cell_margins(c0, top=60, bottom=60, left=100, right=100)
        set_cell_margins(c1, top=60, bottom=60, left=100, right=100)
        
        pk = c0.paragraphs[0]
        pk.paragraph_format.space_after = Pt(0)
        rk = pk.add_run(k)
        rk.bold = True
        rk.font.name = "Calibri"
        rk.font.size = Pt(9.5)
        rk.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
        
        pv = c1.paragraphs[0]
        pv.paragraph_format.space_after = Pt(0)
        rv = pv.add_run(v)
        rv.font.name = "Calibri"
        rv.font.size = Pt(9.5)
        rv.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    doc.add_paragraph().paragraph_format.space_after = Pt(8)

    # -------------------------------------------------------------
    # 1. EXECUTIVE SUMMARY & VENTURE THESIS
    # -------------------------------------------------------------
    h1 = doc.add_heading("1. Executive Summary & Strategic Venture Thesis", level=1)
    h1.paragraph_format.space_before = Pt(14)
    h1.paragraph_format.space_after = Pt(6)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(16)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.15
    p.add_run(
        "India's 12.8 million registered MSMEs, wholesalers, and distributors run their daily commercial operations "
        "almost exclusively over WhatsApp. Orders, pricing inquiries, credit renegotiations, dispatch schedules, and "
        "dispute resolutions are handled across 3 to 15 fragmented WhatsApp numbers held by counter staff, field sales reps, "
        "and warehouse managers. While agile for front-line communication, this creates an acute operational crisis for the "
        "business owner (\"Sethji\"): zero centralized visibility, rampant revenue leakage (8–15% missed/unattended inquiries), "
        "and untracked credit risk."
    )

    add_callout(
        doc,
        "THE ANTI-DISRUPTION PRINCIPLE: Never attempt to force an Indian trader or his field staff to abandon WhatsApp "
        "for a standalone CRM or mobile app. They will resist, falsify data, or abandon it within two weeks. "
        "ZuvoCloud treats WhatsApp as the universal input terminal and layers an ambient, deterministic AI operating system on top.",
        "THE CORE VENTURE THESIS"
    )

    # Key Strategic Differentiators Table
    h2 = doc.add_heading("1.1 Core Strategic Differentiators", level=2)
    h2.paragraph_format.space_before = Pt(10)
    h2.paragraph_format.space_after = Pt(4)
    for r in h2.runs:
        r.font.name = "Arial"
        r.font.size = Pt(13)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    diff_table = doc.add_table(rows=5, cols=3)
    diff_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    diff_table.autofit = False
    diff_table.columns[0].width = Inches(1.8)
    diff_table.columns[1].width = Inches(2.3)
    diff_table.columns[2].width = Inches(2.4)
    set_table_borders(diff_table)

    headers = ["Dimension", "Traditional Approach / Generic Chatbot", "ZuvoCloud AI Solution (IIM-A Blueprint)"]
    for col_idx, text in enumerate(headers):
        cell = diff_table.cell(0, col_idx)
        set_cell_background(cell, "1B365D")
        set_cell_margins(cell, top=80, bottom=80, left=100, right=100)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        run.bold = True
        run.font.name = "Calibri"
        run.font.size = Pt(9.5)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    diff_rows = [
        ("User Workflow", "Forced login to complex desktop CRM/App.", "Zero UI change: Staff continues messaging on WhatsApp."),
        ("AI Role & Trust", "Autonomous chatbot talking to customers directly.", "Silent operational copilot: extracts entities & equips the owner."),
        ("Business Truth", "LLM hallucinates stock & price discounts.", "Deterministic tools: Backend code & DB are sole truth."),
        ("Multi-Worker Ops", "Single generic chatbot number for entire company.", "Maps N individual worker WhatsApp numbers to 1 tenant.")
    ]

    for row_idx, data in enumerate(diff_rows, start=1):
        bg = "F7FAFC" if row_idx % 2 == 1 else "FFFFFF"
        for col_idx, text in enumerate(data):
            cell = diff_table.cell(row_idx, col_idx)
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=60, bottom=60, left=100, right=100)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(text)
            run.font.name = "Calibri"
            run.font.size = Pt(9)
            if col_idx == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
            else:
                run.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # -------------------------------------------------------------
    # 2. TARGET CUSTOMER & MARKET SEGMENTATION (JAIPUR FIRST)
    # -------------------------------------------------------------
    h1 = doc.add_heading("2. Target Customer Profile (ICP) & Market Sizing", level=1)
    h1.paragraph_format.space_before = Pt(14)
    h1.paragraph_format.space_after = Pt(6)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(16)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.15
    p.add_run(
        "Commercial success dictates extreme geographic and vertical focus before horizontal expansion. "
        "Jaipur represents an ideal sandbox: 85,000+ active trade/manufacturing entities clustered in specialized industrial zones, "
        "combined with local operational presence through the co-founder's on-ground availability for physical onboarding."
    )

    # Vertical Evaluation Matrix
    vert_table = doc.add_table(rows=4, cols=4)
    vert_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    vert_table.autofit = False
    vert_table.columns[0].width = Inches(1.8)
    vert_table.columns[1].width = Inches(1.6)
    vert_table.columns[2].width = Inches(1.6)
    vert_table.columns[3].width = Inches(1.5)
    set_table_borders(vert_table)

    vheaders = ["Evaluation Metric", "Vertical A: Building Materials & Hardware (VKI)", "Vertical B: Textiles & Garments (Sanganer)", "Vertical C: FMCG & Foods Stockists"]
    for col_idx, text in enumerate(vheaders):
        cell = vert_table.cell(0, col_idx)
        set_cell_background(cell, "1B365D")
        set_cell_margins(cell, top=80, bottom=80, left=80, right=80)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        run.bold = True
        run.font.name = "Calibri"
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    vrows = [
        ("Daily Order Volume", "High (20–80 orders/day)", "Moderate (5–20 customized batches)", "Very High (100–300 rapid line items)"),
        ("Credit Risk Criticality", "Extremely Severe (30–90 day credit cycle)", "Moderate (Advance + balance on delivery)", "Low / Strict (7-day cash cycle)"),
        ("Willingness to Pay (WTP)", "₹5,000 – ₹10,000 / month (Highest)", "₹4,000 – ₹8,000 / month", "₹3,000 – ₹5,000 / month"),
    ]

    for row_idx, data in enumerate(vrows, start=1):
        bg = "F7FAFC" if row_idx % 2 == 1 else "FFFFFF"
        for col_idx, text in enumerate(data):
            cell = vert_table.cell(row_idx, col_idx)
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=60, bottom=60, left=80, right=80)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(text)
            run.font.name = "Calibri"
            run.font.size = Pt(8.5)
            if col_idx == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
            elif col_idx == 1:
                run.bold = True
                run.font.color.rgb = RGBColor(0x0D, 0x94, 0x88)
            else:
                run.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # -------------------------------------------------------------
    # 3. PRODUCT REQUIREMENTS DOCUMENT (PRD) & DETAILED USE CASES
    # -------------------------------------------------------------
    h1 = doc.add_heading("3. Comprehensive Product Requirements Document (PRD)", level=1)
    h1.paragraph_format.space_before = Pt(14)
    h1.paragraph_format.space_after = Pt(6)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(16)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.15
    p.add_run(
        "The ZuvoCloud platform is engineered into six modular functional subsystems to guarantee horizontal scale, "
        "deterministic business safety, and strict multi-tenant isolation."
    )

    modules = [
        ("Module 1: Multi-Tenant Tenant & Worker Number Management", 
         "• Tenant Provisioning: UUIDv4 tenant identification, business profile, default currency (INR), and credit terms.\n"
         "• Multi-Worker Binding: Mapping of N distinct WhatsApp Business Numbers (Meta Cloud API) to 1 tenant workspace.\n"
         "• Role-Based Access Control (RBAC): Strict server-side authorization separating Owner (Aggregated Pulse), Manager (Approval limits), and Sales Workers (Isolated lines)."),
        
        ("Module 2: Inbound Ingestion & Pre-Processing Pipeline",
         "• Webhook Security: Cryptographic HMAC-SHA256 signature verification on all incoming Meta webhooks.\n"
         "• Ingestion Idempotency: Redis-backed message deduplication (using wamid) to prevent double-processing on network retries.\n"
         "• Media & Voice Normalization: Inbound audio notes (.ogg/.opus/.aac) streamed through Speech-to-Text (STT) for conversational text parsing."),
        
        ("Module 3: Multi-Lingual Hinglish AI Entity Extraction",
         "• Intent Classification: Real-time classification into CREATE_ORDER, INQUIRY_PRICE_STOCK, PAYMENT_COMMITMENT, COMPLAINT_RETURN, and GENERAL_CHAT.\n"
         "• Named Entity Recognition (NER): Structured JSON output capturing Customer Account, SKU Name, Quantity, Units, Requested Rates, and Urgency.\n"
         "• Low-Confidence Guardrails: Intent confidence < 0.80 flags record for human review rather than executing automated steps."),
        
        ("Module 4: Deterministic MCP Business Tool Bus",
         "• Zero-Hallucination Source of Truth: AI is strictly barred from inventing prices or approving credit. It calls backend tools: "
         "get_customer_profile(), search_item_catalog(), check_stock(), evaluate_credit_standing(), create_draft_order().\n"
         "• Hard Credit Limit Formula: If [Current Outstanding + Draft Orders + New Order] > Credit Limit, trigger PENDING_APPROVAL and alert the Owner immediately."),
        
        ("Module 5: Owner Command Center & Live Dashboard",
         "• Universal Live Feed: Aggregated, real-time message stream across all 5+ worker lines in one unified screen.\n"
         "• Multi-Filter Engine: One-click filtering by Worker, Customer Account, Intent Tag, and Approval Status.\n"
         "• 8:00 PM Daily Business Pulse: AI-generated evening briefing highlighting Total Orders Value, Pending Quotations, Bad Debts, and Open Disputes."),

        ("Module 6: Multi-Turn Conversational Commerce, Dynamic Catalog Inquiries & Human Approval Gate",
         "• Multi-Lingual Natural Interaction: Seamless understanding across Hinglish, Hindi, and English (e.g., 'Bhaiya ek double bed aur table ka rate batao', 'What is price of 1 bed and table?', 'एक बेड और टेबल का क्या दाम है?').\n"
         "• Contextual Discovery & Clarification: Rather than dumping arbitrary items, AI proactively asks polite clarifying questions:\n"
         "    1. Size / Dimension Preference: (e.g., King size vs. Queen size bed; 4-seater vs. 6-seater study table).\n"
         "    2. Budget & Material Preference: (e.g., Solid Sheesham wood vs. Engineered wood; budget under ₹25,000).\n"
         "• Rich Catalog Dispatch: Invokes search_item_catalog() tool to retrieve verified SKUs and sends rich product cards with photos, specifications, and official price list.\n"
         "• The 80% Confidence & Human Approval Gate: If message intent confidence is < 80% or if customer asks for custom discounts (e.g., 'Dono saath lene par 15% discount do') exceeding allowed threshold (e.g., 5% max auto-discount), the AI pauses automated reply and alerts the Owner on Dashboard/WhatsApp with 1-click Approve/Counter-offer buttons.\n"
         "• Voice Notes Handling Staging: Phase 1 supports Inbound Voice Note STT transcription with structured text/media replies; Phase 2 introduces full bi-directional Voice TTS (audio answers).")
    ]

    for title, desc in modules:
        h2 = doc.add_heading(title, level=2)
        h2.paragraph_format.space_before = Pt(8)
        h2.paragraph_format.space_after = Pt(2)
        for r in h2.runs:
            r.font.name = "Arial"
            r.font.size = Pt(11.5)
            r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
        
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.15
        p.add_run(desc)

    # -------------------------------------------------------------
    # 4. TECHNICAL ARCHITECTURE & AI ROUTING
    # -------------------------------------------------------------
    h1 = doc.add_heading("4. Technical Architecture, Schema & AI Routing", level=1)
    h1.paragraph_format.space_before = Pt(14)
    h1.paragraph_format.space_after = Pt(6)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(16)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    # AI Routing Table
    ai_table = doc.add_table(rows=5, cols=4)
    ai_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    ai_table.autofit = False
    ai_table.columns[0].width = Inches(1.1)
    ai_table.columns[1].width = Inches(1.8)
    ai_table.columns[2].width = Inches(2.2)
    ai_table.columns[3].width = Inches(1.4)
    set_table_borders(ai_table)

    ai_headers = ["Tier Level", "Model / Technology", "Workload Scope", "Cost per 1k Msgs"]
    for col_idx, text in enumerate(ai_headers):
        cell = ai_table.cell(0, col_idx)
        set_cell_background(cell, "1B365D")
        set_cell_margins(cell, top=80, bottom=80, left=80, right=80)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        run.bold = True
        run.font.name = "Calibri"
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    ai_rows = [
        ("Tier 0", "Deterministic Code / Regex", "Standard greetings ('Hi', 'Thanks'), exact command keywords.", "₹0.00 (Zero API cost)"),
        ("Tier 1", "Local / Small Model (Qwen 1.5B)", "Spam filtering, language detection, simple status checks.", "₹2.50 / 1k messages"),
        ("Tier 2", "Frontier Cloud (Gemini Flash)", "Hinglish Intent, Entity Extraction (NER), MCP Tool Calling.", "₹25.00 / 1k messages"),
        ("Tier 3", "Premium Cloud (Claude / GPT-4o)", "Deep dispute reconciliation, multi-document complex quotes.", "₹350.00 / 1k msgs (On-Demand)")
    ]

    for row_idx, data in enumerate(ai_rows, start=1):
        bg = "F7FAFC" if row_idx % 2 == 1 else "FFFFFF"
        for col_idx, text in enumerate(data):
            cell = ai_table.cell(row_idx, col_idx)
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=60, bottom=60, left=80, right=80)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(text)
            run.font.name = "Calibri"
            run.font.size = Pt(8.5)
            if col_idx == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
            else:
                run.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # -------------------------------------------------------------
    # 5. MINIMUM POC REQUIREMENTS & DEMO ACCEPTANCE
    # -------------------------------------------------------------
    h1 = doc.add_heading("5. Minimum Viable POC Scope & Acceptance Gates", level=1)
    h1.paragraph_format.space_before = Pt(14)
    h1.paragraph_format.space_after = Pt(6)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(16)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.15
    p.add_run(
        "The Proof of Concept (POC) is strictly designed for commercial conversion and technical validation with zero "
        "wasteful over-engineering."
    )

    poc_table = doc.add_table(rows=10, cols=3)
    poc_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    poc_table.autofit = False
    poc_table.columns[0].width = Inches(0.8)
    poc_table.columns[1].width = Inches(2.7)
    poc_table.columns[2].width = Inches(3.0)
    set_table_borders(poc_table)

    pheaders = ["Gate", "POC Acceptance Requirement", "Rigorous Verification Criteria"]
    for col_idx, text in enumerate(pheaders):
        cell = poc_table.cell(0, col_idx)
        set_cell_background(cell, "1B365D")
        set_cell_margins(cell, top=80, bottom=80, left=80, right=80)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        run.bold = True
        run.font.name = "Calibri"
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    poc_gates = [
        ("G1", "Tenant Logical Isolation", "Tenant B API calls injected with Tenant A UUID fail with HTTP 403 Forbidden."),
        ("G2", "5-Worker Number Ingestion", "5 distinct WhatsApp numbers simultaneously ingest to 1 business tenant with correct worker attribution."),
        ("G3", "Hinglish Order Extraction", "Extracts \"Sharma Traders ko 50 box pipe kal bhej do\" into structured JSON schema."),
        ("G4", "Deterministic Credit Rule", "Order exceeding customer credit limit transitions to PENDING_APPROVAL; no auto-create."),
        ("G5", "Unified Owner Command Center", "Live web dashboard displays real-time aggregated chat feed under 3.5s latency."),
        ("G6", "Multi-Worker Line Segmentation & Customer 360° Thread Isolation", "Filter by Worker isolates employee line; Filter by Customer unifies all worker chats into 1 chronological feed (Detailed in 5.1)."),
        ("G7", "Immutable Audit Trail", "All AI tool invocations snapshot parameters into audit_logs table."),
        ("G8", "Conversational Catalog & 80% Confidence Gate", "Multi-turn inquiry (bed/table) asks size/budget, sends photos/rates. If confidence < 80% or discount > 5% -> Human Owner Alert."),
        ("G9", "Inbound Voice Note STT Ingestion", "Voice notes (.ogg/.opus) transcribed to text and processed through NLP pipeline.")
    ]

    for row_idx, data in enumerate(poc_gates, start=1):
        bg = "F7FAFC" if row_idx % 2 == 1 else "FFFFFF"
        for col_idx, text in enumerate(data):
            cell = poc_table.cell(row_idx, col_idx)
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=60, bottom=60, left=80, right=80)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(text)
            run.font.name = "Calibri"
            run.font.size = Pt(8.5)
            if col_idx == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
            else:
                run.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # -------------------------------------------------------------
    # 5.1 DEEP DIVE ON GATE G6 (USER CLARITY REQUIREMENT)
    # -------------------------------------------------------------
    h2 = doc.add_heading("5.1 Comprehensive Deep-Dive: Understanding Gate G6 (Multi-Worker Line Segmentation & 360° Customer Thread Isolation)", level=2)
    h2.paragraph_format.space_before = Pt(10)
    h2.paragraph_format.space_after = Pt(4)
    for r in h2.runs:
        r.font.name = "Arial"
        r.font.size = Pt(12)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p_g6 = doc.add_paragraph()
    p_g6.paragraph_format.space_after = Pt(6)
    p_g6.paragraph_format.line_spacing = 1.15
    p_g6.add_run(
        "Why is Gate G6 the core operational backbone of ZuvoCloud?\n"
        "In a standard Indian trading business, 5 different employees (e.g., Ramesh at Counter, Suresh in Field Sales, Mukesh in Godown) "
        "chat with clients across 5 separate physical WhatsApp numbers. This creates severe operational chaos:\n"
        "1. Employee Accountability Void: The owner cannot see what individual workers are committing to.\n"
        "2. Fragmented Client History: Customer 'Sharma Traders' chats with Suresh for price discounts, Ramesh for placing orders, "
        "and Mukesh for delivery dispatch. Their interaction history is split across 3 separate mobile phones."
    )

    g6_features = [
        ("1. Worker-Level Isolation & Audit View (Staff Accountability): ",
         "Owner clicks '[Worker: Ramesh (Counter)]'. The dashboard instantly displays ONLY conversations and quotes handled on Ramesh's line (+91 98290-XXXXX). "
         "Furthermore, worker Ramesh himself can only see his own assigned threads (preventing employees from stealing client lists)."),
        
        ("2. Customer 360° Unified Timeline (Eliminating Blindspots): ",
         "Owner clicks '[Customer: Sharma Traders]'. The system aggregates all historical and live interactions Sharma Traders had across ALL 5 worker WhatsApp lines "
         "into one single chronological thread (e.g., 10:15 AM Suresh discussed 10% discount -> 02:30 PM Ramesh finalized order -> 05:00 PM Mukesh dispatched goods)."),
        
        ("3. Zero Data Crosstalk Guarantee: ",
         "Messages from Worker A's phone never leak into Worker B's personal feed, while the Owner retains 100% centralized God-mode visibility.")
    ]

    for label, desc in g6_features:
        p_pt = doc.add_paragraph(style='List Bullet')
        p_pt.paragraph_format.space_after = Pt(3)
        p_pt.paragraph_format.line_spacing = 1.15
        r_lbl = p_pt.add_run(label)
        r_lbl.bold = True
        r_lbl.font.name = "Calibri"
        r_lbl.font.size = Pt(9.5)
        r_lbl.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
        
        r_desc = p_pt.add_run(desc)
        r_desc.font.name = "Calibri"
        r_desc.font.size = Pt(9.5)
        r_desc.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    # 15-Minute Demo Script
    add_callout(
        doc,
        "THE 15-MINUTE JAIPUR 'SETHJI' DEMO SCRIPT:\n"
        "• Min 0–3: Ask the owner: 'Sethji, aapke 5 sales reps din bhar WhatsApp pe 200 baatein karte hain. "
        "Aapko kaise pata chalta hai kaunsa order miss ho gaya ya kis party ka credit cross ho gaya?'\n"
        "• Min 3–7: Send two simulated WhatsApp messages from staff phones (one valid order, one exceeding credit limit, and one catalog inquiry).\n"
        "• Min 7–12: Open the Owner Dashboard on an iPad: Show Order 1 auto-captured in Green, Order 2 highlighted in Amber as "
        "'CREDIT BREACH: Held for Owner Approval', and Inquiry 3 with photo cards and size clarification.\n"
        "• Min 12–15: Demonstrate 1-click WhatsApp approval and ask: 'Agar ye software aapka mahine ka 1 galat dispatch aur 2 missed orders bacha le, "
        "toh kya ₹5,000/month ka investment reasonable hai?'",
        "SALES CONVERSION PLAYBOOK"
    )

    # -------------------------------------------------------------
    # 6. PRICING STRUCTURE & COMMERCIAL MODEL
    # -------------------------------------------------------------
    h1 = doc.add_heading("6. Commercial Pricing Strategy & Monetization", level=1)
    h1.paragraph_format.space_before = Pt(14)
    h1.paragraph_format.space_after = Pt(6)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(16)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    price_table = doc.add_table(rows=4, cols=4)
    price_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    price_table.autofit = False
    price_table.columns[0].width = Inches(1.6)
    price_table.columns[1].width = Inches(1.6)
    price_table.columns[2].width = Inches(1.7)
    price_table.columns[3].width = Inches(1.6)
    set_table_borders(price_table)

    prheaders = ["Plan Feature", "Starter Growth", "Business Pro (Core Plan)", "Enterprise Distributor"]
    for col_idx, text in enumerate(prheaders):
        cell = price_table.cell(0, col_idx)
        set_cell_background(cell, "1B365D")
        set_cell_margins(cell, top=80, bottom=80, left=80, right=80)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        run.bold = True
        run.font.name = "Calibri"
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    pr_rows = [
        ("Monthly Subscription", "₹2,999 / month", "₹6,999 / month (Recommended)", "₹14,999 / month"),
        ("Annual Pricing", "₹36,000 / year", "₹84,000 / year", "₹1,80,000 / year"),
        ("Included Capacity", "Up to 3 WhatsApp lines\n2,500 AI Inbound Msgs/mo\nBasic Order Extraction", 
                              "Up to 8 WhatsApp lines\n10,000 AI Inbound Msgs/mo\nFull Tool Bus & Owner Pulse", 
                              "Up to 20 WhatsApp lines\n30,000 AI Inbound Msgs/mo\nVoice STT & Tally Two-Way Sync")
    ]

    for row_idx, data in enumerate(pr_rows, start=1):
        bg = "F7FAFC" if row_idx % 2 == 1 else "FFFFFF"
        for col_idx, text in enumerate(data):
            cell = price_table.cell(row_idx, col_idx)
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=60, bottom=60, left=80, right=80)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(text)
            run.font.name = "Calibri"
            run.font.size = Pt(8.5)
            if col_idx == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
            elif col_idx == 2:
                run.bold = True
                run.font.color.rgb = RGBColor(0x0D, 0x94, 0x88)
            else:
                run.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # -------------------------------------------------------------
    # 7. FINANCIAL PLAN & UNIT ECONOMICS
    # -------------------------------------------------------------
    h1 = doc.add_heading("7. Financial Model, Unit Economics & 3-Year P&L", level=1)
    h1.paragraph_format.space_before = Pt(14)
    h1.paragraph_format.space_after = Pt(6)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(16)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    ue_table = doc.add_table(rows=6, cols=3)
    ue_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    ue_table.autofit = False
    ue_table.columns[0].width = Inches(3.2)
    ue_table.columns[1].width = Inches(1.8)
    ue_table.columns[2].width = Inches(1.5)
    set_table_borders(ue_table)

    ue_headers = ["P&L Component", "Monthly Cost / Revenue (INR)", "% of Total Revenue"]
    for col_idx, text in enumerate(ue_headers):
        cell = ue_table.cell(0, col_idx)
        set_cell_background(cell, "1B365D")
        set_cell_margins(cell, top=80, bottom=80, left=80, right=80)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        run.bold = True
        run.font.name = "Calibri"
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    ue_rows = [
        ("Gross Monthly Subscription Revenue (ARPU)", "₹6,999.00", "100.0%"),
        ("Less: GCP Cloud Run & PostgreSQL Allocation", "(₹280.00)", "4.0%"),
        ("Less: Gemini Flash LLM Inference (8k msgs)", "(₹320.00)", "4.6%"),
        ("Less: Logging, Monitoring & Webhook Overhead", "(₹115.00)", "1.6%"),
        ("NET CONTRIBUTION MARGIN (GROSS PROFIT)", "₹6,284.00", "89.8% (Exceptional)")
    ]

    for row_idx, data in enumerate(ue_rows, start=1):
        bg = "F7FAFC" if row_idx % 2 == 1 else "FFFFFF"
        if row_idx == 5:
            bg = "E6FFFA"
        for col_idx, text in enumerate(data):
            cell = ue_table.cell(row_idx, col_idx)
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=60, bottom=60, left=80, right=80)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(text)
            run.font.name = "Calibri"
            run.font.size = Pt(8.5)
            if row_idx == 5:
                run.bold = True
                run.font.color.rgb = RGBColor(0x0D, 0x94, 0x88)
            elif col_idx == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
            else:
                run.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # 3-Year Pro-Forma Income Statement
    h2 = doc.add_heading("7.1 Three-Year Pro-Forma Income Statement (INR Lakhs)", level=2)
    h2.paragraph_format.space_before = Pt(10)
    h2.paragraph_format.space_after = Pt(4)
    for r in h2.runs:
        r.font.name = "Arial"
        r.font.size = Pt(13)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    pl_table = doc.add_table(rows=8, cols=4)
    pl_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    pl_table.autofit = False
    pl_table.columns[0].width = Inches(2.2)
    pl_table.columns[1].width = Inches(1.4)
    pl_table.columns[2].width = Inches(1.4)
    pl_table.columns[3].width = Inches(1.5)
    set_table_borders(pl_table)

    pl_headers = ["Financial Metric (INR Lakhs)", "Year 1 (Jaipur)", "Year 2 (Rajasthan/NCR)", "Year 3 (National Scale)"]
    for col_idx, text in enumerate(pl_headers):
        cell = pl_table.cell(0, col_idx)
        set_cell_background(cell, "1B365D")
        set_cell_margins(cell, top=80, bottom=80, left=80, right=80)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        run.bold = True
        run.font.name = "Calibri"
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    pl_rows = [
        ("Active Paying Tenants (Exit)", "50 Tenants", "250 Tenants", "1,000 Tenants"),
        ("Average Monthly ARPU", "₹5,800", "₹6,400", "₹7,100"),
        ("GROSS ANNUAL REVENUE", "₹24.36 Lakhs", "₹134.40 Lakhs", "₹639.00 Lakhs"),
        ("Total Direct Hosting & AI COGS", "(₹2.92 Lakhs)", "(₹14.78 Lakhs)", "(₹70.29 Lakhs)"),
        ("GROSS PROFIT (Margin %)", "₹21.44 Lakhs (88.0%)", "₹119.62 Lakhs (89.0%)", "₹568.71 Lakhs (89.0%)"),
        ("Total Operating Expenses (OPEX)", "₹24.30 Lakhs", "₹78.00 Lakhs", "₹208.00 Lakhs"),
        ("EBITDA / NET PROFIT (PRE-TAX)", "(₹2.86 Lakhs)", "+₹41.62 Lakhs", "+₹360.71 Lakhs (+56.4%)")
    ]

    for row_idx, data in enumerate(pl_rows, start=1):
        bg = "F7FAFC" if row_idx % 2 == 1 else "FFFFFF"
        if row_idx == 7:
            bg = "FEF3C7" if data[1].startswith("(") else "D1FAE5"
        for col_idx, text in enumerate(data):
            cell = pl_table.cell(row_idx, col_idx)
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=60, bottom=60, left=80, right=80)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(text)
            run.font.name = "Calibri"
            run.font.size = Pt(8.5)
            if row_idx in [3, 5, 7]:
                run.bold = True
            if col_idx == 0:
                run.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
            else:
                run.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # -------------------------------------------------------------
    # 8. GO-TO-MARKET (GTM) & 90-DAY TIMELINE
    # -------------------------------------------------------------
    h1 = doc.add_heading("8. Accelerated 30-Day POC Sprint Execution Plan & Milestones", level=1)
    h1.paragraph_format.space_before = Pt(14)
    h1.paragraph_format.space_after = Pt(6)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(16)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p_gtm = doc.add_paragraph()
    p_gtm.paragraph_format.space_after = Pt(6)
    p_gtm.paragraph_format.line_spacing = 1.15
    p_gtm.add_run(
        "To achieve rapid commercial validation and secure the first 3 paying distributor pilots in Jaipur without unnecessary "
        "engineering overhead, execution is compressed into four high-velocity 7-day sprints."
    )

    timeline_table = doc.add_table(rows=5, cols=3)
    timeline_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    timeline_table.autofit = False
    timeline_table.columns[0].width = Inches(1.3)
    timeline_table.columns[1].width = Inches(3.4)
    timeline_table.columns[2].width = Inches(1.8)
    set_table_borders(timeline_table)

    t_headers = ["Sprint & Days", "Engineering & Product Deliverables", "Validation & Pilot Gate"]
    for col_idx, text in enumerate(t_headers):
        cell = timeline_table.cell(0, col_idx)
        set_cell_background(cell, "1B365D")
        set_cell_margins(cell, top=80, bottom=80, left=80, right=80)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        run.bold = True
        run.font.name = "Calibri"
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    t_rows = [
        ("Sprint 1 (Days 01–07)", 
         "• GCP Cloud Run, Managed PostgreSQL (Cloud SQL), Redis setup in asia-south1.\n"
         "• Multi-tenant schema execution with Row-Level Security (RLS).\n"
         "• Meta Cloud API webhook handler with HMAC-SHA256 & Redis deduplication.", 
         "Milestone 1 Gate:\n5 distinct WhatsApp lines ingest to 1 tenant with accurate worker attribution."),
        
        ("Sprint 2 (Days 08–15)", 
         "• Gemini Flash structured JSON schema for multi-lingual Intent & NER.\n"
         "• Deterministic MCP Tool Bus (search_item_catalog, check_stock, credit_eval).\n"
         "• Multi-turn inquiry workflow (bed/table size & budget) + 80% confidence & >5% discount Human Approval Gate + Inbound Voice STT.", 
         "Milestone 2 Gate:\nOrders extracted; discount/credit held for owner approval."),
        
        ("Sprint 3 (Days 16–22)", 
         "• Responsive React dashboard with real-time SSE live feed across 5 lines.\n"
         "• Implementation of Gate G6: Worker-level filter vs. Customer 360° unified feed.\n"
         "• 1-Click WhatsApp & Web Owner Approval interface + 8:00 PM Daily Pulse.", 
         "Milestone 3 Gate:\nOwner Command Center functional with live G6 filtering & approvals."),
        
        ("Sprint 4 (Days 23–30)", 
         "• Stress testing across 500 real Jaipur distributor chat transcripts (G1-G9).\n"
         "• Cloud Run production hardening, automated backups & audit logging.\n"
         "• Onboarding First 3 Live Jaipur Pilots in Sitapura/VKI with Tally CSV catalog.", 
         "Milestone 4 Gate:\n3 Live Jaipur Distributors running live operations on ZuvoCloud.")
    ]

    for row_idx, data in enumerate(t_rows, start=1):
        bg = "F7FAFC" if row_idx % 2 == 1 else "FFFFFF"
        for col_idx, text in enumerate(data):
            cell = timeline_table.cell(row_idx, col_idx)
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=60, bottom=60, left=80, right=80)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(text)
            run.font.name = "Calibri"
            run.font.size = Pt(8.5)
            if col_idx == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
            elif col_idx == 2:
                run.bold = True
                run.font.color.rgb = RGBColor(0x0D, 0x94, 0x88)
            else:
                run.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    doc.add_paragraph().paragraph_format.space_after = Pt(12)

    # Sign-off Block
    p_sign = doc.add_paragraph()
    p_sign.paragraph_format.space_after = Pt(2)
    r_sign = p_sign.add_run("STRATEGIC SIGN-OFF & GOVERNANCE APPROVAL")
    r_sign.bold = True
    r_sign.font.name = "Calibri"
    r_sign.font.size = Pt(10.5)
    r_sign.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p_names = doc.add_paragraph()
    p_names.paragraph_format.space_after = Pt(0)
    p_names.paragraph_format.line_spacing = 1.15
    p_names.add_run(
        "Prepared By: Senior Venture Strategist & Serial Entrepreneur (IIM Ahmedabad)\n"
        "Executive Leadership: Jitendra (Founder & Chief Architect) & Operations Co-Founder (Jaipur Hub)\n"
        "Entity: ZuvoCloud Pvt. Ltd. | www.zuvocloud.com | All Intellectual Property Rights Reserved."
    )

    out_primary = r"C:\js\zuvo-ai\Zuvocloud_AI_Solution.docx"
    out_master = r"C:\js\zuvo-ai\Zuvocloud_AI_Solution_Master.docx"
    
    saved_paths = []
    try:
        doc.save(out_primary)
        saved_paths.append(out_primary)
    except PermissionError:
        print(f"Notice: {out_primary} is currently locked by another application (e.g. Word).")
    
    doc.save(out_master)
    saved_paths.append(out_master)
    print(f"Successfully generated Word Document at: {saved_paths}")

if __name__ == "__main__":
    build_document()
