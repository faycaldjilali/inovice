import streamlit as st
import google.generativeai as genai
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import os
from dotenv import load_dotenv
import base64
from io import BytesIO
from PIL import Image
import json

# ---------- Load environment and configure Gemini ----------
load_dotenv()
# Use provided key directly (for demo) – better to use env var in production
GEMINI_API_KEY = "AIzaSyAqUUqzc9udziv9LKIzceuo_v8c8pri1oY"  # Replace with your actual key or use os.getenv
genai.configure(api_key=GEMINI_API_KEY)

# ---------- Database setup (same as before) ----------
Base = declarative_base()
engine = create_engine("sqlite:///invoices.db", echo=False)
Session = sessionmaker(bind=engine)

class InvoiceHeader(Base):
    __tablename__ = "invoice_header"
    id = Column(Integer, primary_key=True)
    supplier = Column(String)
    invoice_date = Column(String)          # store as ISO string for simplicity
    total_amount = Column(Float)
    tax = Column(Float, nullable=True)
    image_paths = Column(String)            # comma‑separated list of file paths
    created_at = Column(DateTime, default=datetime.utcnow)
    line_items = relationship("InvoiceLineItem", back_populates="invoice", cascade="all, delete-orphan")

class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"
    id = Column(Integer, primary_key=True)
    header_id = Column(Integer, ForeignKey("invoice_header.id"))
    description = Column(String)
    quantity = Column(Float)
    unit_price = Column(Float)
    amount = Column(Float)
    invoice = relationship("InvoiceHeader", back_populates="line_items")

Base.metadata.create_all(engine)

# ---------- Helper functions ----------
def save_uploaded_images(uploaded_files):
    """Save uploaded images to disk and return a list of file paths."""
    os.makedirs("uploaded_images", exist_ok=True)
    paths = []
    for file in uploaded_files:
        file_path = os.path.join("uploaded_images", file.name)
        with open(file_path, "wb") as f:
            f.write(file.getbuffer())
        paths.append(file_path)
    return paths

def extract_invoice_data_with_gemini(image_paths):
    """
    Send image(s) to Gemini 1.5 Pro and parse the JSON response.
    """
    # Initialize the model
    model = genai.GenerativeModel('models/gemini-2.5-flash')  # or 'gemini-1.5-flash' for faster/cheaper

    # Prepare the prompt
    prompt = (
        "Extract the following information from this invoice image. "
        "Return a valid JSON object with these fields:\n"
        "- supplier (string)\n"
        "- invoice_date (YYYY-MM-DD)\n"
        "- total_amount (number)\n"
        "- tax (number or null)\n"
        "- line_items (array of objects with description, quantity, unit_price, amount)\n\n"
        "Only include the JSON, no other text."
    )

    # Load images as PIL objects
    images = []
    for path in image_paths:
        try:
            img = Image.open(path)
            images.append(img)
        except Exception as e:
            st.error(f"Could not load image {path}: {e}")
            return None

    # Generate content: prompt + images
    try:
        response = model.generate_content([prompt] + images)
        # Extract JSON from response text
        result_text = response.text
        # Sometimes Gemini wraps JSON in ```json ... ```, clean it
        if result_text.startswith("```json"):
            result_text = result_text[7:]
        if result_text.endswith("```"):
            result_text = result_text[:-3]
        result_text = result_text.strip()
        data = json.loads(result_text)
        return data
    except Exception as e:
        st.error(f"Gemini extraction failed: {e}")
        return None

def save_invoice_to_db(extracted_data, image_paths):
    """Store extracted data and image paths in the database."""
    session = Session()
    try:
        header = InvoiceHeader(
            supplier=extracted_data.get("supplier"),
            invoice_date=extracted_data.get("invoice_date"),
            total_amount=extracted_data.get("total_amount"),
            tax=extracted_data.get("tax"),
            image_paths=",".join(image_paths)
        )
        session.add(header)
        session.flush()  # to get header.id

        for item in extracted_data.get("line_items", []):
            line = InvoiceLineItem(
                header_id=header.id,
                description=item.get("description"),
                quantity=item.get("quantity"),
                unit_price=item.get("unit_price"),
                amount=item.get("amount")
            )
            session.add(line)

        session.commit()
        return header.id
    except Exception as e:
        session.rollback()
        st.error(f"Database save failed: {e}")
        return None
    finally:
        session.close()

def get_all_invoices():
    session = Session()
    invoices = session.query(InvoiceHeader).order_by(InvoiceHeader.created_at.desc()).all()
    session.close()
    return invoices

def get_invoice_detail(invoice_id):
    session = Session()
    invoice = session.query(InvoiceHeader).filter_by(id=invoice_id).first()
    session.close()
    return invoice

# ---------- Streamlit UI (same as before) ----------
st.set_page_config(page_title="NexServe Invoice Scanner (Gemini)", layout="centered", initial_sidebar_state="collapsed")

if "page" not in st.session_state:
    st.session_state.page = "upload"

def go_to_upload():
    st.session_state.page = "upload"

def go_to_list():
    st.session_state.page = "list"

def go_to_detail(invoice_id):
    st.session_state.page = "detail"
    st.session_state.selected_invoice = invoice_id

with st.sidebar:
    st.title("NexServe")
    if st.button("📤 Upload New Invoice", use_container_width=True):
        go_to_upload()
    if st.button("📋 View All Invoices", use_container_width=True):
        go_to_list()

# ---------- Upload Page ----------
if st.session_state.page == "upload":
    st.header("📸 Capture or Upload Invoice")
    st.markdown("Take a photo or upload images of your distributor invoice.")

    col1, col2 = st.columns(2)
    with col1:
        use_camera = st.checkbox("Use camera")
    with col2:
        use_upload = st.checkbox("Upload from gallery")

    uploaded_files = []
    if use_camera:
        camera_photo = st.camera_input("Take a picture")
        if camera_photo:
            uploaded_files.append(camera_photo)
    if use_upload:
        gallery_files = st.file_uploader("Choose images", type=["jpg", "jpeg", "png"], accept_multiple_files=True)
        if gallery_files:
            uploaded_files.extend(gallery_files)

    if st.button("Process Invoice", type="primary", use_container_width=True):
        if not uploaded_files:
            st.warning("Please provide at least one image.")
        else:
            with st.spinner("Saving images..."):
                paths = save_uploaded_images(uploaded_files)
            with st.spinner("Extracting data with Gemini..."):
                extracted = extract_invoice_data_with_gemini(paths)
            if extracted:
                with st.spinner("Saving to database..."):
                    invoice_id = save_invoice_to_db(extracted, paths)
                if invoice_id:
                    st.success(f"Invoice #{invoice_id} saved successfully!")
                    if st.button("View this invoice"):
                        go_to_detail(invoice_id)
                    if st.button("Upload another"):
                        go_to_upload()
                else:
                    st.error("Failed to save invoice.")
            else:
                st.error("Extraction failed. Please try again.")

# ---------- Invoice List Page ----------
elif st.session_state.page == "list":
    st.header("📋 All Invoices")
    invoices = get_all_invoices()
    if not invoices:
        st.info("No invoices yet. Upload your first one!")
    else:
        for inv in invoices:
            with st.container(border=True):
                col1, col2, col3 = st.columns([3,2,1])
                with col1:
                    st.markdown(f"**{inv.supplier or 'Unknown'}**")
                    st.caption(f"Date: {inv.invoice_date or 'N/A'}")
                with col2:
                    st.markdown(f"**${inv.total_amount:.2f}**" if inv.total_amount else "")
                with col3:
                    if st.button("View", key=f"view_{inv.id}"):
                        go_to_detail(inv.id)

# ---------- Invoice Detail Page ----------
elif st.session_state.page == "detail":
    invoice_id = st.session_state.get("selected_invoice")
    if not invoice_id:
        st.error("No invoice selected.")
        go_to_list()
    else:
        invoice = get_invoice_detail(invoice_id)
        if not invoice:
            st.error("Invoice not found.")
            go_to_list()
        else:
            st.header(f"Invoice #{invoice.id}")
            st.caption(f"Created: {invoice.created_at.strftime('%Y-%m-%d %H:%M')}")

            # Show invoice images
            if invoice.image_paths:
                st.subheader("📎 Original Images")
                paths = invoice.image_paths.split(",")
                cols = st.columns(min(len(paths), 3))
                for i, path in enumerate(paths):
                    if os.path.exists(path):
                        with cols[i % 3]:
                            st.image(path, use_container_width=True)
                    else:
                        st.warning(f"Image not found: {path}")

            # Show extracted data
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Supplier:** {invoice.supplier or 'N/A'}")
                st.markdown(f"**Invoice Date:** {invoice.invoice_date or 'N/A'}")
            with col2:
                st.markdown(f"**Total Amount:** ${invoice.total_amount:.2f}" if invoice.total_amount else "**Total:** N/A")
                st.markdown(f"**Tax:** ${invoice.tax:.2f}" if invoice.tax else "**Tax:** N/A")

            # Line items
            if invoice.line_items:
                st.subheader("Line Items")
                data = []
                for item in invoice.line_items:
                    data.append({
                        "Description": item.description,
                        "Qty": item.quantity,
                        "Unit Price": f"${item.unit_price:.2f}" if item.unit_price else "",
                        "Amount": f"${item.amount:.2f}" if item.amount else ""
                    })
                st.dataframe(data, use_container_width=True, hide_index=True)
            else:
                st.info("No line items extracted.")

            if st.button("← Back to list"):
                go_to_list()
