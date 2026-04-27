# services/facture_pdf_service.py — version avec logo à droite, client sous pharmacie
import os
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from services.date_utils import utcnow


def _deduire_unite(prix_unitaire, produit) -> tuple[str, str]:
    """
    Déduit l'unité de vente et construit le label de quantité.
    Retourne (unite_code, unite_label) parmi :
      ('boite', 'boîte(s)'), ('blister', 'blister(s)'), ('piece', 'pièce(s)')
    """
    if not produit:
        return ('piece', 'pièce(s)')

    try:
        pu   = Decimal(str(prix_unitaire or 0))
        pb   = Decimal(str(produit.prix_vente or 0))
        qpb  = produit.quantite_par_boite   or 1
        ppb  = produit.pieces_par_plaquette or 1
        pp   = (pb / qpb).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        ppc  = (pb / qpb / ppb).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        tol  = Decimal('1')   # tolérance arrondi 1 unité de devise

        if abs(pu - pb) <= tol:
            return ('boite', 'boîte(s)')
        if abs(pu - pp) <= tol:
            return ('blister', 'blister(s)')
        if abs(pu - ppc) <= tol:
            return ('piece', 'pièce(s)')
    except Exception:
        pass
    return ('piece', 'pièce(s)')


def _formater_quantite(quantite: int, unite_code: str, produit) -> str:
    """
    Convertit la quantité (toujours en pièces dans la DB) en affichage lisible.
    """
    if not produit:
        return f"{quantite} pcs"
    try:
        qpb = produit.quantite_par_boite   or 1
        ppb = produit.pieces_par_plaquette or 1

        if unite_code == 'boite':
            nb = quantite / (qpb * ppb)
            if nb == int(nb):
                return f"{int(nb)} boîte{'s' if int(nb) > 1 else ''}"
            return f"{quantite} pcs"

        if unite_code == 'blister':
            nb = quantite / ppb
            if nb == int(nb):
                return f"{int(nb)} blister{'s' if int(nb) > 1 else ''}"
            return f"{quantite} pcs"

        return f"{quantite} pcs"

    except Exception:
        return f"{quantite} pcs"


def generer_pdf_facture(facture, pharmacie, client, vente, details, db) -> str | None:
    """
    Génère un PDF de facture A4.
    Layout :
      ┌──────────────────────────────────────────┐
      │ PHARMACIE (infos)            [LOGO]      │
      │                                          │
      │ FACTURÉ À : Nom client, etc.             │
      ├──────────────────────────────────────────┤
      │ FACTURE N° X/2025    Réf: ...  Date: ... │
      ├──────────────────────────────────────────┤
      │ Désignation │ Unité │ Qté │ P.U │ Total  │
      ├─────────────┼───────┼─────┼─────┼────────┤
      │ ...         │       │     │     │        │
      ├──────────────────────────────────────────┤
      │                          HT: ...         │
      │                          TVA: ...        │
      │                          TOTAL TTC: ...  │
      └──────────────────────────────────────────┘
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle,
            Paragraph, Spacer, HRFlowable, Image as RLImage,
            KeepTogether,
        )
        from models.models import Produit

        os.makedirs("uploads/factures", exist_ok=True)
        filepath = f"uploads/factures/{facture.code}.pdf"

        doc = SimpleDocTemplate(
            filepath, pagesize=A4,
            rightMargin=1.8*cm, leftMargin=1.8*cm,
            topMargin=1.8*cm,   bottomMargin=1.8*cm,
        )
        styles = getSampleStyleSheet()

        # ── Couleurs ─────────────────────────────────
        C_BLEU   = colors.HexColor('#1a5276')
        C_GRIS   = colors.HexColor('#566573')
        C_LIGNES = colors.HexColor('#eaf2ff')
        C_GRILLE = colors.HexColor('#bdc3c7')
        C_BG_CLI = colors.HexColor('#f0f4f8')

        # ── Styles texte ─────────────────────────────
        titre_ph = ParagraphStyle(
            'titre_ph', parent=styles['Normal'],
            fontSize=16, fontName='Helvetica-Bold',
            textColor=C_BLEU, spaceAfter=2, leading=18,
        )
        info_ph = ParagraphStyle(
            'info_ph', parent=styles['Normal'],
            fontSize=9, textColor=C_GRIS, leading=13,
        )
        label_cli = ParagraphStyle(
            'label_cli', parent=styles['Normal'],
            fontSize=8, fontName='Helvetica-Bold',
            textColor=C_GRIS, spaceAfter=4,
        )
        nom_cli = ParagraphStyle(
            'nom_cli', parent=styles['Normal'],
            fontSize=11, fontName='Helvetica-Bold',
            textColor=C_BLEU, spaceAfter=2,
        )
        info_cli = ParagraphStyle(
            'info_cli', parent=styles['Normal'],
            fontSize=9, textColor=C_GRIS, leading=13,
        )
        ss_titre = ParagraphStyle(
            'ss_titre', parent=styles['Normal'],
            fontSize=9, textColor=C_GRIS, leading=12,
        )

        # ── Devise ───────────────────────────────────
        devise_code = getattr(pharmacie, 'devise', 'MGA') or 'MGA'
        sym_map = {
            'MGA': 'Ar', 'EUR': '€',   'USD': '$',
            'GBP': '£',  'CHF': 'CHF', 'XOF': 'CFA', 'XAF': 'FCFA',
        }
        sym = sym_map.get(devise_code, devise_code)

        def fmt(val) -> str:
            try:
                n = float(val or 0)
                if devise_code == 'MGA':
                    return f"{round(n):,} {sym}".replace(',', ' ')
                return f"{n:,.2f} {sym}"
            except Exception:
                return f"0 {sym}"

        story = []

        # ════════════════════════════════════════════
        # 1. EN-TÊTE : Pharmacie (gauche) + Logo (droite)
        # ════════════════════════════════════════════

        # Colonne gauche : toutes les infos pharmacie
        col_gauche = []
        col_gauche.append(Paragraph(pharmacie.nom if pharmacie else "Pharmacie", titre_ph))
        for attr, lbl in [('adresse',''), ('telephone','Tél'), ('email','Email'), ('nif','NIF'), ('stat','STAT')]:
            val = getattr(pharmacie, attr, None) if pharmacie else None
            if val:
                col_gauche.append(Paragraph(
                    f"<b>{lbl} :</b> {val}" if lbl else val, info_ph
                ))

        # Colonne droite : logo seulement
        col_droite = []
        logo_path = getattr(pharmacie, 'logo', None) if pharmacie else None
        if logo_path:
            p = logo_path.lstrip('/')
            if os.path.exists(p):
                try:
                    img = RLImage(p, width=2.8*cm, height=2.8*cm, kind='proportional')
                    col_droite.append(img)
                except Exception:
                    pass

        # Tableau à 2 colonnes : gauche (pharmacie) / droite (logo)
        t_logo = Table([[col_gauche, col_droite]], colWidths=[13*cm, 4.4*cm])
        t_logo.setStyle(TableStyle([
            ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',   (0, 0), (0, 0),   0),
            ('RIGHTPADDING',  (0, 0), (0, 0),   0),
            ('ALIGN',         (1, 0), (1, 0),   'RIGHT'),
        ]))
        story.append(t_logo)
        story.append(Spacer(1, 0.3*cm))

        # ── Bloc client (sous la pharmacie, sur toute la largeur) ──
        client_flow = []
        client_flow.append(Paragraph("FACTURÉ À", label_cli))

        if client:
            client_flow.append(Paragraph(client.nom or "—", nom_cli))
            if client.telephone:
                client_flow.append(Paragraph(f"<b>Tél :</b> {client.telephone}", info_cli))
            if client.email:
                client_flow.append(Paragraph(f"<b>Email :</b> {client.email}", info_cli))
            if getattr(client, 'code', None):
                client_flow.append(Paragraph(f"<b>N° :</b> {client.code}", info_cli))
            if client.adresse:
                client_flow.append(Paragraph(f"<b>Adresse :</b> {client.adresse}", info_cli))
        else:
            client_flow.append(Paragraph("Client anonyme", info_cli))

        t_client = Table([[client_flow]], colWidths=[17.4*cm])
        t_client.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), C_BG_CLI),
            ('BOX',        (0, 0), (-1, -1), 0.8, C_BLEU),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING',(0, 0), (-1, -1), 12),
            ('TOPPADDING',  (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 8),
        ]))
        story.append(t_client)
        story.append(Spacer(1, 0.4*cm))
        story.append(HRFlowable(width="100%", thickness=2, color=C_BLEU))
        story.append(Spacer(1, 0.4*cm))

        # ════════════════════════════════════════════
        # 2. INFOS FACTURE (référence, date, échéance)
        # ════════════════════════════════════════════
        type_map   = {"vente": "FACTURE", "avoir": "AVOIR", "proforma": "PROFORMA"}
        type_label = type_map.get(getattr(facture, 'type_facture', 'vente'), 'FACTURE')
        annee      = facture.date_facture.year if facture.date_facture else utcnow().year
        date_str   = facture.date_facture.strftime('%d/%m/%Y') if facture.date_facture else '—'

        titre_fac = ParagraphStyle(
            'titre_fac', fontSize=14, fontName='Helvetica-Bold',
            textColor=C_BLEU,
        )
        info_fac = ParagraphStyle(
            'info_fac', fontSize=9, textColor=C_GRIS,
        )

        row_fac = [
            Paragraph(f"{type_label} N° {facture.numero_facture}/{annee}", titre_fac),
            Paragraph(f"Réf : {facture.code}", info_fac),
            Paragraph(f"Date : {date_str}", info_fac),
        ]
        if facture.date_echeance:
            ech = facture.date_echeance
            ech_str = ech.strftime('%d/%m/%Y') if hasattr(ech, 'strftime') else str(ech)
            row_fac.append(Paragraph(f"Échéance : {ech_str}", info_fac))

        t_fac = Table([row_fac], colWidths=[7*cm] + [3.8*cm] * (len(row_fac) - 1))
        t_fac.setStyle(TableStyle([
            ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(t_fac)
        story.append(Spacer(1, 0.5*cm))

        # ════════════════════════════════════════════
        # 3. TABLEAU DES PRODUITS
        # ════════════════════════════════════════════
        entete_style = ParagraphStyle(
            'ent', fontSize=9, fontName='Helvetica-Bold',
            textColor=colors.white, leading=12,
        )
        headers = [
            Paragraph("Désignation",          entete_style),
            Paragraph("Unité",                entete_style),
            Paragraph("Qté",                  entete_style),
            Paragraph(f"Prix unit. ({sym})",  entete_style),
            Paragraph(f"Total ({sym})",        entete_style),
        ]
        table_data = [headers]

        if details:
            for d in details:
                produit_obj = None
                nom_prod    = f"Produit #{d.id_produit}"
                try:
                    produit_obj = db.query(Produit).filter(Produit.id == d.id_produit).first()
                    if produit_obj:
                        nom_prod = produit_obj.nom
                except Exception:
                    pass

                unite_code, unite_label = _deduire_unite(d.prix_unitaire, produit_obj)
                qte_label = _formater_quantite(d.quantite, unite_code, produit_obj)

                table_data.append([
                    nom_prod,
                    unite_label,
                    qte_label,
                    fmt(d.prix_unitaire),
                    fmt(d.total_ligne),
                ])

        elif vente:
            table_data.append([
                f"Vente {getattr(vente, 'code', '')}",
                "—",
                "1",
                fmt(getattr(vente, 'total', 0)),
                fmt(getattr(vente, 'total', 0)),
            ])

        t_prod = Table(
            table_data,
            colWidths=[6.5*cm, 2.5*cm, 2*cm, 3*cm, 3.4*cm],
        )
        t_prod.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0),  (-1, 0),  C_BLEU),
            ('TEXTCOLOR',     (0, 0),  (-1, 0),  colors.white),
            ('FONTNAME',      (0, 0),  (-1, 0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0, 0),  (-1, -1), 9),
            ('ALIGN',         (2, 0),  (-1, -1), 'RIGHT'),
            ('ALIGN',         (0, 0),  (1, -1),  'LEFT'),
            ('ROWBACKGROUNDS',(0, 1),  (-1, -1), [colors.white, C_LIGNES]),
            ('GRID',          (0, 0),  (-1, -1), 0.5, C_GRILLE),
            ('LINEBELOW',     (0, 0),  (-1, 0),  1, C_BLEU),
            ('BOTTOMPADDING', (0, 0),  (-1, -1), 5),
            ('TOPPADDING',    (0, 0),  (-1, -1), 5),
            ('LEFTPADDING',   (0, 0),  (-1, -1), 6),
            ('RIGHTPADDING',  (0, 0),  (-1, -1), 6),
            ('WORDWRAP',      (0, 1),  (0, -1),  True),
        ]))
        story.append(KeepTogether(t_prod))
        story.append(Spacer(1, 0.5*cm))

        # ════════════════════════════════════════════
        # 4. TOTAUX
        # ════════════════════════════════════════════
        totaux = []
        remise   = float(getattr(facture, 'montant_remise', 0) or 0)
        taux_tva = float(getattr(facture, 'taux_tva', 0) or 0)

        if remise > 0:
            totaux.append([
                Paragraph(f"Remise :", ss_titre),
                Paragraph(f"- {fmt(remise)}", ParagraphStyle('r', fontSize=9, textColor=colors.red, alignment=2)),
            ])
        totaux.append([
            Paragraph("Montant HT :", ss_titre),
            Paragraph(fmt(getattr(facture, 'montant_ht', 0)),
                      ParagraphStyle('ht', fontSize=9, textColor=C_GRIS, alignment=2)),
        ])
        if taux_tva > 0:
            totaux.append([
                Paragraph(f"TVA ({taux_tva:.1f}%) :", ss_titre),
                Paragraph(fmt(getattr(facture, 'montant_tva', 0)),
                          ParagraphStyle('tva', fontSize=9, textColor=C_GRIS, alignment=2)),
            ])

        total_label_style = ParagraphStyle(
            'tot_l', fontSize=11, fontName='Helvetica-Bold',
            textColor=colors.white,
        )
        total_val_style = ParagraphStyle(
            'tot_v', fontSize=12, fontName='Helvetica-Bold',
            textColor=colors.white, alignment=2,
        )
        totaux.append([
            Paragraph("TOTAL TTC :", total_label_style),
            Paragraph(fmt(getattr(facture, 'montant_ttc', 0)), total_val_style),
        ])

        t_totaux = Table(totaux, colWidths=[5*cm, 4*cm])
        t_totaux.setStyle(TableStyle([
            ('ALIGN',         (0, 0),  (0, -1),  'LEFT'),
            ('ALIGN',         (1, 0),  (1, -1),  'RIGHT'),
            ('VALIGN',        (0, 0),  (-1, -1), 'MIDDLE'),
            ('FONTSIZE',      (0, 0),  (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0),  (-1, -1), 4),
            ('TOPPADDING',    (0, 0),  (-1, -1), 4),
            ('BACKGROUND',    (0, -1), (-1, -1), C_BLEU),
            ('LEFTPADDING',   (0, -1), (-1, -1), 8),
            ('RIGHTPADDING',  (0, -1), (-1, -1), 8),
            ('TOPPADDING',    (0, -1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 6),
            ('LINEABOVE',     (0, -1), (-1, -1), 1, C_BLEU),
        ]))

        t_totaux_wrapper = Table(
            [["", t_totaux]],
            colWidths=[8.4*cm, 9*cm],
        )
        t_totaux_wrapper.setStyle(TableStyle([
            ('ALIGN',   (1, 0), (1, 0), 'RIGHT'),
            ('VALIGN',  (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(t_totaux_wrapper)

        # ════════════════════════════════════════════
        # 5. NOTES + PIED
        # ════════════════════════════════════════════
        if getattr(facture, 'notes', None):
            story.append(Spacer(1, 0.5*cm))
            story.append(Paragraph(
                f"<b>Notes :</b> {facture.notes}", ss_titre
            ))

        story.append(Spacer(1, 1*cm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_GRILLE))
        story.append(Spacer(1, 0.2*cm))
        story.append(Paragraph(
            "Merci pour votre confiance.",
            ParagraphStyle('merci', fontSize=9, textColor=C_GRIS, alignment=1),
        ))

        doc.build(story)
        return f"/{filepath}"

    except ImportError as e:
        print(f"ReportLab non installé : {e}. Commande : pip install reportlab")
        return None
    except Exception as e:
        import traceback
        print(f"Erreur génération PDF facture : {e}")
        traceback.print_exc()
        return None