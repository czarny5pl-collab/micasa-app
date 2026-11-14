# -*- coding: utf-8 -*-
import os, io
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib import colors

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "micasa.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Property(Base):
    __tablename__ = "properties"
    id = Column(Integer, primary_key=True, index=True)
    ref = Column(String, unique=True, index=True, nullable=False)
    nie = Column(String, nullable=False)
    nie2 = Column(String, default="")
    nie3 = Column(String, default="")
    nie4 = Column(String, default="")
    share = Column(Float, default=100.0)
    share2 = Column(Float, default=0.0)
    share3 = Column(Float, default=0.0)
    share4 = Column(Float, default=0.0)
    residency = Column(String, default="eu")
    valor_catastral = Column(Float, default=0.0)
    valor_catastral_suelo = Column(Float, default=0.0)
    purchase_price = Column(Float, default=0.0)
    purchase_date = Column(String, default="")
    purchase_expenses = Column(Float, default=0.0)
    revised_catastral = Column(Boolean, default=False)
    city = Column(String, default="")
    nickname = Column(String, default="")

class Sale(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True, index=True)
    property_ref = Column(String, ForeignKey("properties.ref"), nullable=False)
    sale_price = Column(Float, default=0.0)
    sale_date = Column(String, default="")
    sale_expenses = Column(Float, default=0.0)
    improvements = Column(Float, default=0.0)
    plusvalia_coeff = Column(Float, default=0.0)

class Rental(Base):
    __tablename__ = "rentals"
    id = Column(Integer, primary_key=True, index=True)
    property_ref = Column(String, ForeignKey("properties.ref"), nullable=False)
    rental_income = Column(Float, default=0.0)
    rental_days = Column(Integer, default=0)
    empty_days = Column(Integer, default=0)
    expenses = Column(Float, default=0.0)
    mortgage_interest = Column(Float, default=0.0)
    depreciation = Column(Float, default=0.0)
    insurance = Column(Float, default=0.0)
    other_expenses = Column(Float, default=0.0)

def init_db():
    Base.metadata.create_all(bind=engine)

def calc_imputed(vc, rev, days, res, share):
    rate = 0.011 if rev else 0.02
    tr = 0.19 if res == "eu" else 0.24
    base = vc * rate * (days / 365) * (share / 100)
    return {"base": round(base, 2), "tax": round(base * tr, 2)}

def calc_rental(inc, exp, res, de, vc, rev, share):
    tr = 0.19 if res == "eu" else 0.24
    if res == "eu":
        rt = max(0, inc - exp) * tr
        w = None
    else:
        rt = inc * tr
        w = "Rezydent spoza UE: 24% od przychodu brutto BEZ odliczen."
    imp = calc_imputed(vc, rev, de, res, share)
    return {"rental_tax": round(rt, 2), "imputed_tax": imp["tax"], "total_tax": round(rt + imp["tax"], 2), "warning": w}

def calc_sale(prop, sale):
    tr = 0.19 if prop.residency == "eu" else 0.24
    acq = prop.purchase_price + prop.purchase_expenses + sale.improvements
    trans = sale.sale_price - sale.expenses
    gain = trans - acq
    if gain <= 0:
        return {"capital_gain": 0, "tax": 0, "estimated_plusvalia": 0, "warning": "Brak zysku. Plusvalia = 0.", "acquisition_value": round(acq, 2), "transmission_value": round(trans, 2)}
    tax = gain * tr
    try:
        years = max(1, int((datetime.strptime(sale.sale_date, "%Y-%m-%d") - datetime.strptime(prop.purchase_date, "%Y-%m-%d")).days / 365))
        coeff = min(sale.plusvalia_coeff, 0.30 * min(years, 20))
        pv = prop.valor_catastral_suelo * coeff
        if pv > gain: pv = 0
    except:
        pv = 0
    return {"acquisition_value": round(acq, 2), "transmission_value": round(trans, 2), "capital_gain": round(gain, 2), "tax": round(tax, 2), "estimated_plusvalia": round(pv, 2), "warning": "Wydatki musza byc udokumentowane."}

def gen_pdf(data, title="Modelo 210"):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, h - 2*cm, f"Mi Casa en Espana - {title}")
    c.setFont("Helvetica", 10)
    c.drawString(2*cm, h - 2.5*cm, f"Data: {data.get('date','')} | NIE: {data.get('nie','')} | Ref: {data.get('property_ref','')}")
    y = h - 3.5*cm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2*cm, y, "Szczegoly kalkulacji")
    c.setFont("Helvetica", 11)
    y -= 1*cm
    for k, v in data.items():
        if k in ["date","nie","property_ref","warning"]: continue
        c.drawString(2*cm, y, k.replace("_"," ").title() + ":")
        c.drawRightString(15*cm, y, f"{v} EUR" if isinstance(v,(int,float)) else str(v))
        y -= 0.7*cm
        if y < 4*cm: break
    if data.get("warning"):
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(colors.red)
        c.drawString(2*cm, 3*cm, f"UWAGA: {data['warning']}")
    c.save()
    buf.seek(0)
    return buf

app = FastAPI(title="Mi Casa en Espana 2026")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class PropIn(BaseModel):
    ref: str; nie: str; nie2: str = ""; nie3: str = ""; nie4: str = ""
    share: float = 100.0; share2: float = 0.0; share3: float = 0.0; share4: float = 0.0
    residency: str = "eu"; valor_catastral: float = 0.0; valor_catastral_suelo: float = 0.0
    purchase_price: float = 0.0; purchase_date: str = ""; purchase_expenses: float = 0.0
    revised_catastral: bool = False; city: str = ""; nickname: str = ""

class SaleIn(BaseModel):
    property_ref: str; sale_price: float; sale_date: str
    sale_expenses: float = 0.0; improvements: float = 0.0; plusvalia_coeff: float = 0.0

class RentalIn(BaseModel):
    property_ref: str; rental_income: float; rental_days: int = 0; empty_days: int = 0
    expenses: float = 0.0; mortgage_interest: float = 0.0; depreciation: float = 0.0
    insurance: float = 0.0; other_expenses: float = 0.0

from contextlib import asynccontextmanager
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Mi Casa en Espana 2026", lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
def root():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Brak index.html</h1>"

@app.get("/manifest.json")
def manifest():
    return FileResponse("manifest.json", media_type="application/manifest+json")

@app.get("/icon-192.png")
def icon192():
    if os.path.exists("icon-192.png"):
        return FileResponse("icon-192.png", media_type="image/png")
    return FileResponse("index.html", media_type="text/html")

@app.get("/icon-512.png")
def icon512():
    if os.path.exists("icon-512.png"):
        return FileResponse("icon-512.png", media_type="image/png")
    return FileResponse("index.html", media_type="text/html")

@app.post("/api/property/")
def create_prop(p: PropIn, db: Session = Depends(get_db)):
    if not p.nie or p.nie[0].upper() not in ["X","Y","Z"]:
        raise HTTPException(400, "NIE musi zaczynac sie od X, Y lub Z")
    if db.query(Property).filter(Property.ref == p.ref).first():
        raise HTTPException(400, "Ref juz istnieje")
    db.add(Property(**p.dict()))
    db.commit()
    return {"message": "Zapisano"}

@app.get("/api/properties/")
def list_props(db: Session = Depends(get_db)):
    result = []
    for x in db.query(Property).all():
        owners = [{"nie": x.nie, "share": x.share}]
        if x.nie2: owners.append({"nie": x.nie2, "share": x.share2})
        if x.nie3: owners.append({"nie": x.nie3, "share": x.share3})
        if x.nie4: owners.append({"nie": x.nie4, "share": x.share4})
        result.append({"ref": x.ref, "nickname": x.nickname, "nie": x.nie, "residency": x.residency, "valor": x.valor_catastral, "city": x.city, "owners": owners})
    return result

@app.post("/api/calculate/rental/{ref}")
def calc_r(ref: str, rn: RentalIn, db: Session = Depends(get_db)):
    p = db.query(Property).filter(Property.ref == ref).first()
    if not p: raise HTTPException(404, "Nie znaleziono")
    db.add(Rental(property_ref=ref, **rn.dict()))
    db.commit()
    rental = db.query(Rental).filter(Rental.property_ref==ref).order_by(Rental.id.desc()).first()
    te = rn.expenses + rn.mortgage_interest + rn.depreciation + rn.insurance + rn.other_expenses
    r = calc_rental(rn.rental_income, te, p.residency, rn.empty_days, p.valor_catastral, p.revised_catastral, p.share)
    r.update({"date": datetime.now().strftime("%Y-%m-%d"), "nie": p.nie, "property_ref": ref, "rental_id": rental.id})
    return r

@app.post("/api/calculate/sale/{ref}")
def calc_s(ref: str, s: SaleIn, db: Session = Depends(get_db)):
    p = db.query(Property).filter(Property.ref == ref).first()
    if not p: raise HTTPException(404, "Nie znaleziono")
    db.add(Sale(property_ref=ref, **s.dict()))
    db.commit()
    sale = db.query(Sale).filter(Sale.property_ref==ref).order_by(Sale.id.desc()).first()
    r = calc_sale(p, sale)
    r.update({"date": datetime.now().strftime("%Y-%m-%d"), "nie": p.nie, "property_ref": ref, "sale_id": sale.id})
    return r

@app.get("/api/download_pdf/rental/{rid}")
def pdf_r(rid: int, db: Session = Depends(get_db)):
    r = db.query(Rental).filter(Rental.id==rid).first()
    p = db.query(Property).filter(Property.ref==r.property_ref).first() if r else None
    if not r or not p: raise HTTPException(404, "Nie znaleziono")
    te = r.expenses + r.mortgage_interest + r.depreciation + r.insurance + r.other_expenses
    d = calc_rental(r.rental_income, te, p.residency, r.empty_days, p.valor_catastral, p.revised_catastral, p.share)
    d.update({"date": datetime.now().strftime("%Y-%m-%d"), "nie": p.nie, "property_ref": p.ref})
    return StreamingResponse(gen_pdf(d, "Modelo 210"), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=210_{p.ref}.pdf"})

@app.get("/api/download_pdf/sale/{sid}")
def pdf_s(sid: int, db: Session = Depends(get_db)):
    s = db.query(Sale).filter(Sale.id==sid).first()
    p = db.query(Property).filter(Property.ref==s.property_ref).first() if s else None
    if not s or not p: raise HTTPException(404, "Nie znaleziono")
    d = calc_sale(p, s)
    d.update({"date": datetime.now().strftime("%Y-%m-%d"), "nie": p.nie, "property_ref": p.ref})
    return StreamingResponse(gen_pdf(d, "Modelo 210-H"), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=210H_{p.ref}.pdf"})