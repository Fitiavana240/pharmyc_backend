# services/facture_pdf_service.py — Pharmy-C v4.2

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
    Exemple : 40 pièces avec unité 'boite' (qpb=2, ppb=10) → '2 boîte(s)'
    Exemple : 30 pièces avec unité 'blister' (ppb=10) → '3 blister(s)'
    Exemple : 5 pièces avec unité 'piece' → '5 pièce(s)'
    Cas mixte (pièces non divisibles par ppb) → '43 pcs'
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
            # non entier → fallback pièces
            return f"{quantite} pcs"

        if unite_code == 'blister':
            nb = quantite / ppb
            if nb == int(nb):
                return f"{int(nb)} blister{'s' if int(nb) > 1 else ''}"
            return f"{quantite} pcs"

        # pièce
        return f"{quantite} pcs"

    except Exception:
        return f"{quantite} pcs"


def generer_pdf_facture(facture, pharmacie, client, vente, details, db) -> str | None:
    """
    Génère un PDF de facture A4.
    Layout :
      ┌──────────────────────────────────────────┐
      │ [LOGO]  Pharmacie         FACTURÉ À      │
      │         Adresse           Nom client     │
      │         Tél / Email       Tél / Email    │
      │                           N° client      │
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
        # 1. EN-TÊTE : pharmacie (gauche) + client (droite)
        # ════════════════════════════════════════════

        # ── Colonne gauche : logo + pharmacie ───────
        col_g = []

        logo_path = getattr(pharmacie, 'logo', None) if pharmacie else None
        if logo_path:
            p = logo_path.lstrip('/')
            if os.path.exists(p):
                try:
                    img = RLImage(p, width=2.8*cm, height=2.8*cm, kind='proportional')
                    col_g.append(img)
                    col_g.append(Spacer(1, 0.15*cm))
                except Exception:
                    pass

        col_g.append(Paragraph(pharmacie.nom if pharmacie else "Pharmacie", titre_ph))
        for attr, lbl in [('adresse',''), ('telephone','Tél'), ('email','Email'), ('nif','NIF'), ('stat','STAT')]:
            val = getattr(pharmacie, attr, None) if pharmacie else None
            if val:
                col_g.append(Paragraph(
                    f"<b>{lbl} :</b> {val}" if lbl else val, info_ph
                ))

        # ── Colonne droite : client ──────────────────
        col_d = []
        col_d.append(Paragraph("FACTURÉ À", label_cli))

        if client:
            col_d.append(Paragraph(client.nom or "—", nom_cli))
            if client.telephone:
                col_d.append(Paragraph(f"<b>Tél :</b> {client.telephone}", info_cli))
            if client.email:
                col_d.append(Paragraph(f"<b>Email :</b> {client.email}", info_cli))
            # Numéro identité client = code client (CLI-XXXXXX)
            if getattr(client, 'code', None):
                col_d.append(Paragraph(f"<b>N° :</b> {client.code}", info_cli))
            if client.adresse:
                col_d.append(Paragraph(f"<b>Adresse :</b> {client.adresse}", info_cli))
        else:
            col_d.append(Paragraph("Client anonyme", info_cli))

        # Tableau 2 colonnes : gauche 10cm / droite 7.4cm
        t_header = Table([[col_g, col_d]], colWidths=[10*cm, 7.4*cm])
        t_header.setStyle(TableStyle([
            ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',   (0, 0), (0, 0),   0),
            ('RIGHTPADDING',  (0, 0), (0, 0),   6),
            # Encadré client avec fond légèrement coloré
            ('BOX',           (1, 0), (1, 0),   0.8, C_BLEU),
            ('BACKGROUND',    (1, 0), (1, 0),   C_BG_CLI),
            ('LEFTPADDING',   (1, 0), (1, 0),   10),
            ('RIGHTPADDING',  (1, 0), (1, 0),   10),
            ('TOPPADDING',    (1, 0), (1, 0),   8),
            ('BOTTOMPADDING', (1, 0), (1, 0),   8),
        ]))
        story.append(t_header)
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
        #    Colonnes : Désignation | Unité | Qté | Prix unit. | Total
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
                # Récupérer le produit pour calculer l'unité
                produit_obj = None
                nom_prod    = f"Produit #{d.id_produit}"
                try:
                    produit_obj = db.query(Produit).filter(Produit.id == d.id_produit).first()
                    if produit_obj:
                        nom_prod = produit_obj.nom
                except Exception:
                    pass

                # Déduire l'unité de vente depuis le prix_unitaire
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
            # Fallback si pas de détails
            table_data.append([
                f"Vente {getattr(vente, 'code', '')}",
                "—",
                "1",
                fmt(getattr(vente, 'total', 0)),
                fmt(getattr(vente, 'total', 0)),
            ])

        # Largeurs : Désignation 6.5cm | Unité 2.5cm | Qté 2cm | Prix 3cm | Total 3.4cm
        t_prod = Table(
            table_data,
            colWidths=[6.5*cm, 2.5*cm, 2*cm, 3*cm, 3.4*cm],
        )
        t_prod.setStyle(TableStyle([
            # En-tête
            ('BACKGROUND',    (0, 0),  (-1, 0),  C_BLEU),
            ('TEXTCOLOR',     (0, 0),  (-1, 0),  colors.white),
            ('FONTNAME',      (0, 0),  (-1, 0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0, 0),  (-1, -1), 9),
            # Alignement
            ('ALIGN',         (2, 0),  (-1, -1), 'RIGHT'),   # Qté, Prix, Total → droite
            ('ALIGN',         (0, 0),  (1, -1),  'LEFT'),    # Désignation, Unité → gauche
            # Couleurs alternées
            ('ROWBACKGROUNDS',(0, 1),  (-1, -1), [colors.white, C_LIGNES]),
            # Grille
            ('GRID',          (0, 0),  (-1, -1), 0.5, C_GRILLE),
            ('LINEBELOW',     (0, 0),  (-1, 0),  1, C_BLEU),
            # Padding
            ('BOTTOMPADDING', (0, 0),  (-1, -1), 5),
            ('TOPPADDING',    (0, 0),  (-1, -1), 5),
            ('LEFTPADDING',   (0, 0),  (-1, -1), 6),
            ('RIGHTPADDING',  (0, 0),  (-1, -1), 6),
            # Wrap texte dans Désignation
            ('WORDWRAP',      (0, 1),  (0, -1),  True),
        ]))
        story.append(KeepTogether(t_prod))
        story.append(Spacer(1, 0.5*cm))

        # ════════════════════════════════════════════
        # 4. TOTAUX (alignés à droite)
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

        # Tableau totaux décalé à droite (largeur 9cm sur 17.4cm total)
        t_totaux = Table(totaux, colWidths=[5*cm, 4*cm])
        t_totaux.setStyle(TableStyle([
            ('ALIGN',         (0, 0),  (0, -1),  'LEFT'),
            ('ALIGN',         (1, 0),  (1, -1),  'RIGHT'),
            ('VALIGN',        (0, 0),  (-1, -1), 'MIDDLE'),
            ('FONTSIZE',      (0, 0),  (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0),  (-1, -1), 4),
            ('TOPPADDING',    (0, 0),  (-1, -1), 4),
            # Ligne Total TTC en bleu
            ('BACKGROUND',    (0, -1), (-1, -1), C_BLEU),
            ('LEFTPADDING',   (0, -1), (-1, -1), 8),
            ('RIGHTPADDING',  (0, -1), (-1, -1), 8),
            ('TOPPADDING',    (0, -1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 6),
            # Séparateur au-dessus du total
            ('LINEABOVE',     (0, -1), (-1, -1), 1, C_BLEU),
        ]))

        # Aligner à droite via un tableau conteneur
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
        # 5. NOTES + PIED DE PAGE
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