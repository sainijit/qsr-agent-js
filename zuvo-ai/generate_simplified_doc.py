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

def set_cell_margins(cell, top=80, bottom=80, left=120, right=120):
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

def add_callout(doc, text, title="CORE PRINCIPLE"):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Inches(6.5)
    cell = table.cell(0, 0)
    set_cell_background(cell, "F0F4F8")
    set_cell_margins(cell, top=120, bottom=120, left=160, right=140)
    
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
    p.paragraph_format.space_after = Pt(2)
    run_t = p.add_run(f"[{title}] ")
    run_t.bold = True
    run_t.font.name = "Calibri"
    run_t.font.size = Pt(10)
    run_t.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
    
    run_txt = p.add_run(text)
    run_txt.font.name = "Calibri"
    run_txt.font.size = Pt(9.5)
    run_txt.font.italic = True
    run_txt.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)

def build_simplified_document():
    doc = Document()
    
    # Margins
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)
        
        header = section.header
        hp = header.paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        hrun = hp.add_run("Zuvocloud AI Solution | Simplified Requirements (Point-by-Point)")
        hrun.font.name = "Calibri"
        hrun.font.size = Pt(8.5)
        hrun.font.color.rgb = RGBColor(0x71, 0x80, 0x96)
        
        footer = section.footer
        fp = footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        frun = fp.add_run("ZuvoCloud Pvt. Ltd. — Executive Requirements Document — Confidential")
        frun.font.name = "Calibri"
        frun.font.size = Pt(8.5)
        frun.font.color.rgb = RGBColor(0xA0, 0xAE, 0xC0)

    # Document Header Title
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = Pt(0)
    p_title.paragraph_format.space_after = Pt(2)
    p_title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    r_title = p_title.add_run("Zuvocloud AI Solution")
    r_title.bold = True
    r_title.font.name = "Arial"
    r_title.font.size = Pt(24)
    r_title.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p_sub = doc.add_paragraph()
    p_sub.paragraph_format.space_before = Pt(0)
    p_sub.paragraph_format.space_after = Pt(12)
    r_sub = p_sub.add_run("Simplified Product Requirements Document (PRD) — Point-by-Point Functional & Technical Specification")
    r_sub.font.name = "Calibri"
    r_sub.font.size = Pt(12)
    r_sub.font.color.rgb = RGBColor(0x4A, 0x55, 0x68)

    # Meta Table Box
    meta_table = doc.add_table(rows=3, cols=2)
    meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta_table.autofit = False
    meta_table.columns[0].width = Inches(2.0)
    meta_table.columns[1].width = Inches(4.5)
    set_table_borders(meta_table, color="CBD5E0", sz="6")
    
    meta_data = [
        ("Venture Lead / Author:", "Senior Venture Strategist & Serial Entrepreneur (IIM Ahmedabad Alumnus)"),
        ("Target Market & Hub:", "Jaipur Trading Clusters (Sitapura / VKI / Mansarovar) → Pan-India SME Distribution"),
        ("Document Edition:", "Simplified Point-by-Point Master Release v1.0")
    ]
    
    for i, (k, v) in enumerate(meta_data):
        c0 = meta_table.cell(i, 0)
        c1 = meta_table.cell(i, 1)
        set_cell_background(c0, "F7FAFC")
        set_cell_background(c1, "FFFFFF")
        set_cell_margins(c0, top=50, bottom=50, left=80, right=80)
        set_cell_margins(c1, top=50, bottom=50, left=80, right=80)
        
        pk = c0.paragraphs[0]
        pk.paragraph_format.space_after = Pt(0)
        rk = pk.add_run(k)
        rk.bold = True
        rk.font.name = "Calibri"
        rk.font.size = Pt(9)
        rk.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
        
        pv = c1.paragraphs[0]
        pv.paragraph_format.space_after = Pt(0)
        rv = pv.add_run(v)
        rv.font.name = "Calibri"
        rv.font.size = Pt(9)
        rv.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # Section 1: Executive Summary
    h1 = doc.add_heading("1. Executive Summary & Core Principle", level=1)
    h1.paragraph_format.space_before = Pt(12)
    h1.paragraph_format.space_after = Pt(4)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(14)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p1 = doc.add_paragraph()
    p1.paragraph_format.space_after = Pt(4)
    p1.paragraph_format.line_spacing = 1.15
    p1.add_run(
        "• 1.1 The Core Problem: Indian wholesale distributors run high-volume business over 3 to 15 fragmented staff WhatsApp numbers. "
        "Owners suffer from zero visibility, 8–15% missed/delayed orders, and unmonitored credit risks.\n"
        "• 1.2 The Anti-Disruption Principle: Front-line staff continue using WhatsApp without changing their habit. "
        "ZuvoCloud acts as an ambient AI operating layer, structuring chat data and empowering the owner via a central Command Center.\n"
        "• 1.3 Zero-Hallucination Mandate: LLM (Gemini Flash) is strictly used for language understanding (Hinglish/Hindi/English) and entity extraction. "
        "All business facts (pricing, inventory, credit limits) are 100% deterministic and enforced by backend databases and tools."
    )

    # Section 2: Point-by-Point Functional Requirements
    h1 = doc.add_heading("2. Point-by-Point Functional Requirements", level=1)
    h1.paragraph_format.space_before = Pt(12)
    h1.paragraph_format.space_after = Pt(4)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(14)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    req_sections = [
        ("REQ-1.0: Multi-Tenant Workspace & Role Management", [
            ("REQ-1.1 Tenant Isolation: ", "Every business is provisioned as an independent tenant with a unique tenant_id (UUIDv4). Cross-tenant data leakage is strictly blocked at the database and API layer."),
            ("REQ-1.2 Role-Based Access Control (RBAC): ", "Supports Owner (unrestricted view across all worker feeds), Manager (approval thresholds up to ₹50k), and Worker (isolated view strictly of their own WhatsApp line)."),
            ("REQ-1.3 Business Profile Master: ", "Stores official business profile, GSTIN, currency (INR), payment terms, and default credit limits.")
        ]),
        ("REQ-2.0: WhatsApp Ingestion & Multi-Worker Line Management", [
            ("REQ-2.1 Meta Cloud API Ingestion: ", "Real-time message ingestion via official Meta WhatsApp Business Platform webhooks."),
            ("REQ-2.2 Webhook Security Verification: ", "Cryptographic HMAC-SHA256 signature verification on all inbound payloads."),
            ("REQ-2.3 Ingestion Deduplication: ", "Deduplicates incoming messages using wamid (WhatsApp Message ID) cached in Redis for 24h."),
            ("REQ-2.4 Multi-Number Binding: ", "Connects N distinct worker WhatsApp Business numbers to a single business tenant workspace."),
            ("REQ-2.5 Worker Attribution: ", "Automatically attributes every incoming message to the exact worker receiving the line.")
        ]),
        ("REQ-3.0: Multi-Lingual AI Intelligence & Entity Extraction", [
            ("REQ-3.1 Multi-Lingual NLP Engine: ", "Native comprehension of colloquial Hinglish, Hindi (Devanagari), and Indian English (e.g., 'Sharma Traders ko 50 box pipe kal bhej do')."),
            ("REQ-3.2 5-Class Intent Classification: ", "Classifies messages into CREATE_ORDER, INQUIRY_PRICE_STOCK, PAYMENT_COMMITMENT, COMPLAINT_RETURN, and GENERAL_CHAT."),
            ("REQ-3.3 Named Entity Recognition (NER): ", "Extracts structured JSON payload with customer_name, line_items (product, qty, unit, requested_rate), delivery_date, and confidence_score.")
        ]),
        ("REQ-4.0: Conversational Commerce, Inquiry Clarification & Catalog Quoting", [
            ("REQ-4.1 Multi-Turn Clarification: ", "When customer asks open inquiries (e.g., 'What is the price of a bed and a table?'), AI does not guess. It asks clarifying questions for Size/Dimensions (King vs Queen bed, 4 vs 6-seater table) and Budget/Material (Sheesham vs Engineered wood)."),
            ("REQ-4.2 Automated Catalog Search & Media Dispatch: ", "Calls search_item_catalog() and sends rich WhatsApp product cards with high-res photos, specs, stock, and official price list."),
            ("REQ-4.3 Voice Note Processing Roadmap: ", "Phase 1 transcribes Inbound Voice Notes (.ogg/.opus) via Speech-to-Text (STT) and replies via structured text/photos. Phase 2 introduces full bi-directional Voice TTS (audio answers).")
        ]),
        ("REQ-5.0: Deterministic Business Tools & Guardrail Engine", [
            ("REQ-5.1 Backend MCP Tool Bus: ", "AI calls deterministic backend tools: get_customer_profile(), search_item_catalog(), check_stock_availability(), evaluate_credit_standing(), create_draft_order()."),
            ("REQ-5.2 Credit Limit Formula: ", "If [Current Outstanding + Draft Orders + New Order] > Credit Limit, order status transitions to HELD_FOR_APPROVAL.")
        ]),
        ("REQ-6.0: Human-in-the-Loop & Approval System", [
            ("REQ-6.1 The 80% Confidence Guardrail: ", "If intent/entity confidence < 80%, AI does not post automated replies; flags draft for human review."),
            ("REQ-6.2 Discount Policy Gate: ", "Counter staff auto-discount limit is 5%. If customer asks for 15% discount, AI holds quote and triggers an Owner Approval Alert via WhatsApp/Dashboard."),
            ("REQ-6.3 One-Click Approval Interface: ", "Owner can click [Approve 10%] | [Approve 15%] | [Reject] directly from WhatsApp or the Web Command Center.")
        ]),
        ("REQ-7.0: Owner Command Center & Unified Dashboard (Gate G6)", [
            ("REQ-7.1 Universal Real-Time Feed: ", "Aggregated live stream of all messages, orders, and quotes across all 5+ numbers under 3.5s latency."),
            ("REQ-7.2 Gate G6 Worker Isolation vs Customer 360°: ", "Worker Filter isolates individual employee lines (Ramesh Counter). Customer Filter unifies all 5 worker chats with that party into 1 chronological timeline. Zero crosstalk guarantee."),
            ("REQ-7.3 8:00 PM Daily Business Pulse: ", "AI-synthesized evening summary detailing captured order value, pending quotes, credit breaches, and unresolved complaints.")
        ]),
        ("REQ-8.0: Data Security, Multi-Tenant Isolation & Compliance", [
            ("REQ-8.1 Database Row-Level Security (RLS): ", "PostgreSQL RLS enabled on all tenant tables. Every query enforces WHERE tenant_id = :current_tenant."),
            ("REQ-8.2 India DPDP Act 2023 Compliance: ", "100% data localization with GCP Mumbai (asia-south1) hosting. AES-256 at rest, TLS 1.3 in transit."),
            ("REQ-8.3 Immutable Audit Logging: ", "Every AI tool call, discount approval, and order state change is logged in audit_logs with actor ID and parameter snapshot.")
        ])
    ]

    for sec_title, items in req_sections:
        h2 = doc.add_heading(sec_title, level=2)
        h2.paragraph_format.space_before = Pt(8)
        h2.paragraph_format.space_after = Pt(2)
        for r in h2.runs:
            r.font.name = "Arial"
            r.font.size = Pt(11)
            r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
        
        for label, text in items:
            p = doc.add_paragraph(style='List Bullet')
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.15
            rl = p.add_run(label)
            rl.bold = True
            rl.font.name = "Calibri"
            rl.font.size = Pt(9.5)
            rl.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
            
            rt = p.add_run(text)
            rt.font.name = "Calibri"
            rt.font.size = Pt(9.5)
            rt.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)

    # Section 3: Acceptance Gates (G1 to G9)
    h1 = doc.add_heading("3. Minimum Viable POC Acceptance Gates (G1 to G9)", level=1)
    h1.paragraph_format.space_before = Pt(12)
    h1.paragraph_format.space_after = Pt(4)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(14)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    gate_table = doc.add_table(rows=10, cols=3)
    gate_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    gate_table.autofit = False
    gate_table.columns[0].width = Inches(0.8)
    gate_table.columns[1].width = Inches(2.7)
    gate_table.columns[2].width = Inches(3.0)
    set_table_borders(gate_table)

    gheaders = ["Gate", "POC Acceptance Requirement", "Rigorous Verification Test"]
    for col_idx, text in enumerate(gheaders):
        cell = gate_table.cell(0, col_idx)
        set_cell_background(cell, "1B365D")
        set_cell_margins(cell, top=60, bottom=60, left=80, right=80)
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
        ("G3", "Hinglish Order Extraction", "Extracts 'Sharma Traders ko 50 box pipe kal bhej do' into structured JSON schema."),
        ("G4", "Deterministic Credit Rule", "Order exceeding customer credit limit transitions to PENDING_APPROVAL; no auto-create."),
        ("G5", "Unified Owner Dashboard Rendering", "Live web dashboard displays real-time aggregated chat feed under 3.5s latency."),
        ("G6", "Multi-Worker Line Segmentation & Customer 360° Thread Isolation", "Filter by Worker isolates employee line; Filter by Customer unifies all worker chats into 1 chronological feed."),
        ("G7", "Immutable Audit Trail", "All AI tool invocations snapshot parameters into audit_logs table."),
        ("G8", "Conversational Catalog & 80% Confidence Gate", "Inquires bed/table price -> asks size/budget -> sends photos/rates. If confidence < 80% or discount > 5% -> Human Owner Alert."),
        ("G9", "Inbound Voice Note STT Ingestion", "Voice notes (.ogg/.opus) transcribed to text and processed through NLP pipeline.")
    ]

    for row_idx, data in enumerate(poc_gates, start=1):
        bg = "F7FAFC" if row_idx % 2 == 1 else "FFFFFF"
        for col_idx, text in enumerate(data):
            cell = gate_table.cell(row_idx, col_idx)
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=50, bottom=50, left=80, right=80)
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

    # Section 4: 30-Day Sprint Plan
    h1 = doc.add_heading("4. 30-Day Accelerated POC Execution Plan", level=1)
    h1.paragraph_format.space_before = Pt(12)
    h1.paragraph_format.space_after = Pt(4)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(14)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    t_table = doc.add_table(rows=5, cols=3)
    t_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    t_table.autofit = False
    t_table.columns[0].width = Inches(1.3)
    t_table.columns[1].width = Inches(3.4)
    t_table.columns[2].width = Inches(1.8)
    set_table_borders(t_table)

    thead = ["Sprint & Days", "Engineering & Product Deliverables", "Validation & Pilot Gate"]
    for col_idx, text in enumerate(thead):
        cell = t_table.cell(0, col_idx)
        set_cell_background(cell, "1B365D")
        set_cell_margins(cell, top=60, bottom=60, left=80, right=80)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        run.bold = True
        run.font.name = "Calibri"
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    sprints = [
        ("Sprint 1 (Days 01–07)", 
         "• GCP Cloud Run, Managed PostgreSQL (Cloud SQL asia-south1), Redis setup.\n"
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

    for row_idx, data in enumerate(sprints, start=1):
        bg = "F7FAFC" if row_idx % 2 == 1 else "FFFFFF"
        for col_idx, text in enumerate(data):
            cell = t_table.cell(row_idx, col_idx)
            set_cell_background(cell, bg)
            set_cell_margins(cell, top=50, bottom=50, left=80, right=80)
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

    # Section 5: Pricing & Unit Economics
    h1 = doc.add_heading("5. Commercial Pricing & Unit Economics Summary", level=1)
    h1.paragraph_format.space_before = Pt(12)
    h1.paragraph_format.space_after = Pt(4)
    for r in h1.runs:
        r.font.name = "Arial"
        r.font.size = Pt(14)
        r.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p5 = doc.add_paragraph()
    p5.paragraph_format.space_after = Pt(4)
    p5.paragraph_format.line_spacing = 1.15
    p5.add_run(
        "• 5.1 Starter Growth Plan: ₹2,999 / month (Up to 3 WhatsApp lines, 2,500 AI messages/mo, basic order extraction).\n"
        "• 5.2 Business Pro Plan (Core): ₹6,999 / month (Up to 8 WhatsApp lines, 10,000 AI messages/mo, full MCP tool bus, Owner Command Center, Tally 1-way sync).\n"
        "• 5.3 Enterprise Distributor Plan: ₹14,999 / month (Up to 20 WhatsApp lines, 30,000 AI messages/mo, Voice STT, priority custom rules, dedicated manager).\n"
        "• 5.4 Unit Economics (Business Pro): Revenue = ₹6,999/mo | Direct Hosting & AI COGS = ₹715/mo | Net Contribution Margin = ₹6,284/mo (89.8% Gross Margin)."
    )

    doc.save(r"C:\js\zuvo-ai\Zuvocloud_Simplified_Requirements.docx")
    print("Successfully generated C:\\js\\zuvo-ai\\Zuvocloud_Simplified_Requirements.docx")

if __name__ == "__main__":
    build_simplified_document()
