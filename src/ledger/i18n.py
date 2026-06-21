"""A small, hand-maintained localization layer (user research P2-1 / T9).

English-only UI blocks safety comprehension for the people ledger most needs to
protect, and jargon at the trauma-decision point ("Proceed", an untranslated
content-warning tag) is exactly where a contributor most needs plain, native
language. This module gives the browse server a tiny, dependency-free string
catalog plus a correct ``Accept-Language`` negotiation so the right language is
chosen automatically, with a safe English fallback that never raises.

Design qualities deliberately built in here:

* **No new dependency.** A plain dict catalog and a small parser, standard library
  only, so a community can keep running ledger on one inexpensive box.
* **Fail soft, never blank.** A missing key, a missing language, or a missing
  interpolation argument degrades to English or to the key/tag itself rather than
  raising — a half-translated catalog can never take a page down (robustness).
* **Determinism.** Lookups are pure functions of their arguments; no clock, no
  locale, no global state, so the same request always renders the same string.

No-outing rule: this module holds only generic UI chrome and plain-language glosses
for a *controlled* content-warning vocabulary. It never contains, logs, or formats a
contributor identity or a sealed value; callers pass only UI keys and CW tags.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# The fallback language. Every key is guaranteed to exist here, so any lookup can
# fall back to English and still return a real string (safety: never blank).
DEFAULT_LANG = "en"

# The languages this build ships a catalog for, in preference order. A steward's
# config may serve a subset; ``negotiate`` is told the available set explicitly.
SUPPORTED: tuple[str, ...] = ("en", "es")

# Human-readable language names, in the language's own script (autonym), so a
# language picker reads naturally to a native speaker.
_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Español",
}

# The UI string catalog: language -> key -> template. Every key present in ``en``
# should also exist in every other language; where a translation is genuinely
# missing, ``t`` falls back to the English template so the page still renders.
# "proceed" is intentionally "Continue"/"Continuar", never "Proceed" — at the
# content-warning decision point a neutral, non-coercive verb is part of the safety
# design (user research T9).
_CATALOG: dict[str, dict[str, str]] = {
    "en": {
        "nav_browse": "Browse",
        "nav_search": "Search",
        "nav_status": "Status",
        "nav_about": "About",
        "skip_link": "Skip to main content",
        "search_label": "Search the archive",
        "search_button": "Search",
        "withheld_heading": "Some parts of this record are not shown",
        "withheld_intro": (
            "Parts of this record have been kept private by the contributor or a "
            "steward. You are seeing everything you are allowed to see."
        ),
        "proceed": "Continue",
        "back_to_records": "Back to records",
        "empty_no_matches": "No records match your search.",
        "restricted_notice": "This record has access restrictions.",
        "content_warning_heading": "Content warning",
        "results_list_heading": "Records (list view)",
        "results_table_heading": "Records (table view)",
        "results_showing": "Showing {start}-{end} of {total} record(s).",
        "table_caption": (
            "All records you may view, with their titles, summaries, and whether each "
            "carries a content warning."
        ),
        "col_title": "Title",
        "col_summary": "Summary",
        "no_records_available": "No records are available to you yet.",
        "facet_subjects": "Subjects",
        "facet_types": "Types",
        "facet_languages": "Languages",
        "clear_filters": "Clear filters",
        "sort_label": "Sort by:",
        "sort_relevance": "Relevance",
        "sort_newest": "Newest",
        "sort_oldest": "Oldest",
        "answer_yes": "Yes",
        "answer_no": "No",
        "pager_label": "Pagination",
        "pager_prev": "Previous",
        "pager_next": "Next",
        "pager_position": "Page {number} of {pages}",
        "back_to_archive": "Back to the archive",
        # --- steward console + audit log ---
        "sw_console_heading": "Steward console",
        "sw_submissions_heading": "Submissions awaiting review",
        "sw_submissions_intro": (
            "Contributions arrive sealed — nothing is visible until you publish it. "
            "Publishing opens a record to the visibility the contributor asked for; "
            "withholding holds it for revision. Every choice is recorded."
        ),
        "sw_no_submissions": "No submissions awaiting review.",
        "sw_would_publish_as": "Would publish as:",
        "sw_open_to_read": "Open the record to read it before deciding.",
        "sw_submitted": "submitted {when}",
        "sw_publish_button": "Publish (as requested)",
        "sw_withhold_button": "Withhold",
        "sw_select_label": "Select this submission",
        "sw_bulk_withhold": "Withhold selected",
        "badge_edited_one": "Edited (1 time)",
        "badge_edited_many": "Edited ({count} times)",
        "sw_vis_public": "Public — anyone may read it",
        "sw_vis_community": "Community only — vetted members",
        "sw_vis_sealed": "Sealed — kept private for now",
        "sw_requests_heading": "Open consent & takedown requests",
        "sw_on_record": "on record",
        "sw_request_meta": "{when}, ref {ref}",
        "sw_mark_resolved": "Mark resolved",
        "sw_no_requests": "No open requests.",
        "sw_before_heading": "Before you act",
        "sw_before_access": (
            "You can read access-restricted content to do your work, but content sealed "
            "with the 'sealed' policy — and every contributor's identity — is restricted "
            "even from you. Some records may be sealed above your access; their absence "
            "here does not mean they do not exist."
        ),
        "sw_cli_intro": "Action a request with the audited CLI:",
        "sw_cli_policy_note": "(change access),",
        "sw_cli_cw_note": "(add a content warning) — each records who acted and why.",
        "sw_view_audit": "View the audit log",
        "sw_view_audit_note": "— every recorded action across the archive.",
        "req_kind_withdraw": "withdraw / take down",
        "req_kind_tighten": "tighten access",
        "req_kind_correct": "correct the record",
        "req_kind_contact": "ask a steward to make contact",
        "req_kind_object": "objection from a person named in the record",
        "audit_heading": "Audit log",
        "audit_intro": (
            "Every recorded action across the archive, newest first. This log carries "
            "no contributor identity or sealed value — only what happened, who acted, "
            "and the outcome."
        ),
        "audit_caption": "Recorded actions, newest first",
        "audit_col_when": "When",
        "audit_col_event": "Event",
        "audit_col_outcome": "Outcome",
        "audit_col_agent": "Agent",
        "audit_col_object": "Object",
        "audit_col_detail": "Detail",
        "audit_no_events": "No recorded events yet.",
        "audit_back": "Back to the steward console",
        # --- consent-status lookup (contributor checks a request) ---
        "cs_heading": "Check a request",
        "cs_intro": (
            "Enter the reference code you were given when you filed a consent or "
            "takedown request to see whether a steward has acted on it."
        ),
        "cs_not_found": (
            "We could not find a request with that reference. Check it and try again — "
            "it is the code shown when you filed the request."
        ),
        "cs_status_aria": "Request status",
        "cs_request_label": "Request: {kind}",
        "cs_filed_label": "Filed: {when}",
        "cs_status_label": "Status: {status}",
        "cs_status_open": "Received — a steward has not acted on it yet.",
        "cs_status_acknowledged": "Seen by a steward and under consideration.",
        "cs_status_resolved": "Resolved — a steward has acted on it.",
        "cs_ref_label": "Your request reference",
        "cs_button": "Check status",
        # --- single record page ---
        "rec_cw_review": (
            "This record carries the following content warnings. Review them before continuing."
        ),
        "rec_cw_note": "Content warnings:",
        "rec_content_sr": "Record content.",
        "rec_fields_heading": "Details",
        "rec_catalogue_heading": "Catalogue metadata",
        "rec_files_heading": "Files",
        "rec_withheld_heading": "Withheld",
        "rec_withheld_insider": (
            "Some parts of this record are not available under your current access:"
        ),
        "rec_withheld_outsider_one": (
            "{count} detail is restricted under your current access. If you are a "
            "community member or steward, sign in to see what is withheld and why."
        ),
        "rec_withheld_outsider_many": (
            "{count} details are restricted under your current access. If you are a "
            "community member or steward, sign in to see what is withheld and why."
        ),
        "rec_consent_link": "Are you the contributor? Manage or withdraw your consent",
        "rec_object_link": "Are you named in this record and object to it? Tell a steward",
        "payload_transcript": "Transcript",
        "payload_no_transcript": "No transcript provided for this audio/video.",
        # --- contribution form (the contributor write path) ---
        "contribute_heading": "Contribute to the archive",
        "contribute_intro": (
            "Share a story, an account, or knowledge worth keeping. A steward reviews "
            "every submission before anything is published — your contribution is kept "
            'sealed until then. Use "Preview" to see exactly what a stranger would see '
            "before you submit."
        ),
        "label_title": "Title",
        "label_summary": "Summary (optional)",
        "summary_hint": (
            "One line shown in listings, search results, and the feed. Anyone who can "
            "see this record will see this summary."
        ),
        "details_legend": "Details (optional)",
        "details_hint": (
            "Descriptive details make this record findable by topic and browsable by "
            "facet. Like the summary, they are shown to anyone who can see the record."
        ),
        "label_subject": "Subjects",
        "subject_hint": "Comma-separated topics, e.g. mutual aid, housing.",
        "label_type": "Type",
        "type_hint": "What kind of material this is, e.g. photograph, oral history, flyer.",
        "label_date": "Date",
        "date_hint": "When the material is from, e.g. 1994 or 2021-05-01.",
        "label_language": "Language",
        "language_hint": "The language of the material, e.g. English or Spanish.",
        "label_account": "Your account",
        "cw_legend": "Content warnings (optional)",
        "cw_hint": "Tick anything a reader should be warned about before this is shown.",
        "share_legend": "How should this be shared?",
        "share_hint": (
            "A steward reviews every submission before anything becomes visible — "
            "nothing is published automatically."
        ),
        "file_legend": "Attach a file (optional)",
        "file_hint": (
            "You can attach one image, audio file, or PDF (up to {max} MB). Accepted: "
            "{accepted}. The file is reviewed with the rest of your submission and is "
            "not public until a steward publishes it."
        ),
        "label_file": "File",
        "contact_legend": "Contact (optional, sealed)",
        "contact_hint": (
            "Only a steward with explicit permission can ever see this. It is "
            "encrypted, never shown publicly, and never reveals who contributed a record."
        ),
        "label_name": "Name",
        "label_reach": "How to reach you",
        "button_preview": "Preview what a stranger sees",
        "button_submit": "Submit for review",
        # --- "what a stranger sees" preview panel ---
        "preview_heading": "Preview — what a stranger would see",
        "preview_aria_label": "What a stranger sees",
        "preview_if_published": (
            "If a steward publishes this, a stranger who is not signed in would see:"
        ),
        "preview_content_warnings": "Content warnings: {list}",
        "preview_stranger_nothing_lead": "A stranger sees nothing.",
        "preview_stranger_nothing_detail": (
            " Published as {visibility}, this record would be visible to {audience} — "
            "it would not appear in public browse or search."
        ),
        "preview_audience_community": "community members only",
        "preview_audience_none": "no one yet",
        "preview_sealed_hint": (
            "Your name and contact are never shown to any reader — they are sealed. "
            "They are not in this preview."
        ),
        # --- submission confirmation + self-withdrawal ---
        "thanks_heading": "Thank you — your contribution was received",
        "thanks_status": (
            "It is sealed and waiting for a steward to review it. Nothing you submitted "
            "is public yet, and any contact details you gave are encrypted and will "
            "never be shown."
        ),
        "thanks_claim_heading": "Keep this if you might change your mind",
        "thanks_claim_intro": (
            "While your submission is still waiting for review you can withdraw it "
            "yourself. To do that you will need both of these — keep them private, as "
            "together they let someone withdraw this submission:"
        ),
        "label_reference": "Reference",
        "label_withdrawal_code": "Withdrawal code",
        "thanks_withdraw_before": "To withdraw it, go to",
        "withdrawal_page_link_text": "the withdrawal page",
        "thanks_withdraw_after": "and enter both.",
        "thanks_edit_before": "You can also correct it on",
        "edit_page_link_text": "the edit page",
        "withdraw_heading": "Withdraw a submission",
        "withdraw_intro": (
            "If you contributed something and it is still waiting for review, you can "
            "withdraw it here using the reference and withdrawal code from your "
            "confirmation page. Withdrawing permanently removes the submission and "
            "erases any contact details you sealed with it. Once a steward has "
            "published a record, request a change from its page instead."
        ),
        "withdraw_button": "Withdraw this submission",
        # --- decline / validation messages (shown to the contributor) ---
        "err_title_required": "A title is required.",
        "err_account_required": "An account is required.",
        "err_submission_too_long": "That submission is too long.",
        "err_contact_too_long": "Those contact details are too long.",
        "err_save_failed": "Your contribution could not be saved right now. Please try again.",
        "err_file_too_large": "That file is too large. The limit is {max} MB.",
        "err_file_type": "That file type isn't accepted. Allowed types are: {types}.",
        "err_withdraw_failed": (
            "We could not withdraw a pending submission with that reference and code. "
            "Check both and try again."
        ),
        "err_edit_failed": (
            "We could not find a pending submission with that reference and code. "
            "Check both and try again."
        ),
        # --- edit a pending submission ---
        "edit_heading": "Edit a pending submission",
        "edit_intro": (
            "If your submission is still waiting for review, you can correct it here. "
            "Enter the reference and code from your confirmation, choose “Load my "
            "submission”, make your changes, then save. Once a steward has "
            "published a record, this no longer applies — request a change from its "
            "page instead."
        ),
        "label_code": "Your code",
        "edit_load_button": "Load my submission",
        "edit_save_button": "Save changes",
        "edit_done_heading": "Your changes were saved",
        "edit_done_status": (
            "Your pending submission has been updated. It is still sealed and waiting "
            "for a steward to review it."
        ),
        "withdraw_done_heading": "Your submission was withdrawn",
        "withdraw_done_status": (
            "It has been permanently removed, along with any contact details you had "
            "sealed with it. Nothing from it remains in the archive."
        ),
        "footer_privacy": "Your identity is never shown. Contributors control what is public.",
        "language_label": "Language",
        "visibility_public": "Public — anyone may read it once a steward approves",
        "visibility_community": "Community only — vetted members of this community",
        "visibility_sealed": "Sealed — keep it withheld for now",
    },
    "es": {
        "nav_browse": "Explorar",
        "nav_search": "Buscar",
        "nav_status": "Estado",
        "nav_about": "Acerca de",
        "skip_link": "Saltar al contenido principal",
        "search_label": "Buscar en el archivo",
        "search_button": "Buscar",
        "withheld_heading": "Algunas partes de este registro no se muestran",
        "withheld_intro": (
            "El colaborador o una persona responsable han mantenido privadas algunas "
            "partes de este registro. Está viendo todo lo que tiene permitido ver."
        ),
        "proceed": "Continuar",
        "back_to_records": "Volver a los registros",
        "empty_no_matches": "Ningún registro coincide con su búsqueda.",
        "restricted_notice": "Este registro tiene restricciones de acceso.",
        "content_warning_heading": "Advertencia de contenido",
        "results_list_heading": "Registros (vista de lista)",
        "results_table_heading": "Registros (vista de tabla)",
        "results_showing": "Mostrando {start}-{end} de {total} registro(s).",
        "table_caption": (
            "Todos los registros que puede ver, con sus títulos, resúmenes y si cada uno "
            "lleva una advertencia de contenido."
        ),
        "col_title": "Título",
        "col_summary": "Resumen",
        "no_records_available": "Aún no hay registros disponibles para usted.",
        "facet_subjects": "Temas",
        "facet_types": "Tipos",
        "facet_languages": "Idiomas",
        "clear_filters": "Quitar los filtros",
        "sort_label": "Ordenar por:",
        "sort_relevance": "Relevancia",
        "sort_newest": "Más recientes",
        "sort_oldest": "Más antiguos",
        "answer_yes": "Sí",
        "answer_no": "No",
        "pager_label": "Paginación",
        "pager_prev": "Anterior",
        "pager_next": "Siguiente",
        "pager_position": "Página {number} de {pages}",
        "back_to_archive": "Volver al archivo",
        # --- consola de administración + registro de auditoría ---
        "sw_console_heading": "Consola de administración",
        "sw_submissions_heading": "Envíos a la espera de revisión",
        "sw_submissions_intro": (
            "Las contribuciones llegan selladas: nada es visible hasta que usted lo "
            "publique. Publicar abre un registro a la visibilidad que pidió el "
            "colaborador; retenerlo lo guarda para revisión. Cada decisión se registra."
        ),
        "sw_no_submissions": "No hay envíos a la espera de revisión.",
        "sw_would_publish_as": "Se publicaría como:",
        "sw_open_to_read": "Abra el registro para leerlo antes de decidir.",
        "sw_submitted": "enviado {when}",
        "sw_publish_button": "Publicar (como se solicitó)",
        "sw_withhold_button": "Retener",
        "sw_select_label": "Seleccionar este envío",
        "sw_bulk_withhold": "Retener los seleccionados",
        "badge_edited_one": "Editado (1 vez)",
        "badge_edited_many": "Editado ({count} veces)",
        "sw_vis_public": "Público — cualquiera puede leerlo",
        "sw_vis_community": "Solo la comunidad — miembros verificados",
        "sw_vis_sealed": "Sellado — reservado por ahora",
        "sw_requests_heading": "Solicitudes abiertas de consentimiento y retirada",
        "sw_on_record": "en el registro",
        "sw_request_meta": "{when}, ref {ref}",
        "sw_mark_resolved": "Marcar como resuelta",
        "sw_no_requests": "No hay solicitudes abiertas.",
        "sw_before_heading": "Antes de actuar",
        "sw_before_access": (
            "Puede leer contenido de acceso restringido para hacer su trabajo, pero el "
            "contenido sellado con la política «sealed» —y la identidad de cada "
            "colaborador— está restringido incluso para usted. Algunos registros pueden "
            "estar sellados por encima de su acceso; su ausencia aquí no significa que "
            "no existan."
        ),
        "sw_cli_intro": "Gestione una solicitud con la CLI auditada:",
        "sw_cli_policy_note": "(cambiar acceso),",
        "sw_cli_cw_note": (
            "(añadir una advertencia de contenido); cada uno registra quién actuó y por qué."
        ),
        "sw_view_audit": "Ver el registro de auditoría",
        "sw_view_audit_note": "— cada acción registrada en todo el archivo.",
        "req_kind_withdraw": "retirar / dar de baja",
        "req_kind_tighten": "restringir el acceso",
        "req_kind_correct": "corregir el registro",
        "req_kind_contact": "pedir a una persona responsable que se ponga en contacto",
        "req_kind_object": "objeción de una persona nombrada en el registro",
        "audit_heading": "Registro de auditoría",
        "audit_intro": (
            "Cada acción registrada en todo el archivo, las más recientes primero. Este "
            "registro no contiene ninguna identidad de colaborador ni valor sellado: "
            "solo qué ocurrió, quién actuó y el resultado."
        ),
        "audit_caption": "Acciones registradas, las más recientes primero",
        "audit_col_when": "Cuándo",
        "audit_col_event": "Evento",
        "audit_col_outcome": "Resultado",
        "audit_col_agent": "Agente",
        "audit_col_object": "Objeto",
        "audit_col_detail": "Detalle",
        "audit_no_events": "Aún no hay eventos registrados.",
        "audit_back": "Volver a la consola de administración",
        # --- consulta del estado de una solicitud ---
        "cs_heading": "Consultar una solicitud",
        "cs_intro": (
            "Introduzca el código de referencia que recibió al presentar una solicitud "
            "de consentimiento o de retirada para ver si una persona responsable ha "
            "actuado sobre ella."
        ),
        "cs_not_found": (
            "No se encontró una solicitud con esa referencia. Compruébela e inténtelo "
            "de nuevo: es el código que se mostró cuando presentó la solicitud."
        ),
        "cs_status_aria": "Estado de la solicitud",
        "cs_request_label": "Solicitud: {kind}",
        "cs_filed_label": "Presentada: {when}",
        "cs_status_label": "Estado: {status}",
        "cs_status_open": "Recibida — una persona responsable aún no ha actuado sobre ella.",
        "cs_status_acknowledged": "Vista por una persona responsable y en consideración.",
        "cs_status_resolved": "Resuelta — una persona responsable ha actuado sobre ella.",
        "cs_ref_label": "La referencia de su solicitud",
        "cs_button": "Consultar el estado",
        # --- página de un registro ---
        "rec_cw_review": (
            "Este registro lleva las siguientes advertencias de contenido. Revíselas "
            "antes de continuar."
        ),
        "rec_cw_note": "Advertencias de contenido:",
        "rec_content_sr": "Contenido del registro.",
        "rec_fields_heading": "Detalles",
        "rec_catalogue_heading": "Metadatos de catálogo",
        "rec_files_heading": "Archivos",
        "rec_withheld_heading": "Retenido",
        "rec_withheld_insider": (
            "Algunas partes de este registro no están disponibles con su acceso actual:"
        ),
        "rec_withheld_outsider_one": (
            "{count} detalle está restringido con su acceso actual. Si es miembro de la "
            "comunidad o persona responsable, inicie sesión para ver qué se retiene y "
            "por qué."
        ),
        "rec_withheld_outsider_many": (
            "{count} detalles están restringidos con su acceso actual. Si es miembro de "
            "la comunidad o persona responsable, inicie sesión para ver qué se retiene y "
            "por qué."
        ),
        "rec_consent_link": "¿Es usted el colaborador? Gestione o retire su consentimiento",
        "rec_object_link": (
            "¿Está nombrado en este registro y se opone a él? Avise a una persona responsable"
        ),
        "payload_transcript": "Transcripción",
        "payload_no_transcript": "No se proporcionó transcripción para este audio/vídeo.",
        # --- formulario de contribución ---
        "contribute_heading": "Contribuir al archivo",
        "contribute_intro": (
            "Comparta una historia, un relato o conocimiento que valga la pena "
            "conservar. Una persona responsable revisa cada envío antes de publicar "
            'nada: su contribución se mantiene sellada hasta entonces. Use "Vista '
            'previa" para ver exactamente lo que vería un desconocido antes de enviar.'
        ),
        "label_title": "Título",
        "label_summary": "Resumen (opcional)",
        "summary_hint": (
            "Una línea que se muestra en los listados, los resultados de búsqueda y el "
            "canal. Cualquiera que pueda ver este registro verá este resumen."
        ),
        "details_legend": "Detalles (opcional)",
        "details_hint": (
            "Los detalles descriptivos hacen que este registro se pueda encontrar por "
            "tema y explorar por faceta. Como el resumen, se muestran a cualquiera que "
            "pueda ver el registro."
        ),
        "label_subject": "Temas",
        "subject_hint": "Temas separados por comas, p. ej. ayuda mutua, vivienda.",
        "label_type": "Tipo",
        "type_hint": "Qué clase de material es, p. ej. fotografía, historia oral, folleto.",
        "label_date": "Fecha",
        "date_hint": "De cuándo es el material, p. ej. 1994 o 2021-05-01.",
        "label_language": "Idioma",
        "language_hint": "El idioma del material, p. ej. inglés o español.",
        "label_account": "Su relato",
        "cw_legend": "Advertencias de contenido (opcional)",
        "cw_hint": (
            "Marque todo aquello sobre lo que se deba advertir a quien lo lea antes de mostrarlo."
        ),
        "share_legend": "¿Cómo debería compartirse esto?",
        "share_hint": (
            "Una persona responsable revisa cada envío antes de que algo se haga "
            "visible; nada se publica automáticamente."
        ),
        "file_legend": "Adjuntar un archivo (opcional)",
        "file_hint": (
            "Puede adjuntar una imagen, un archivo de audio o un PDF (hasta {max} MB). "
            "Aceptados: {accepted}. El archivo se revisa junto con el resto de su envío "
            "y no es público hasta que una persona responsable lo publique."
        ),
        "label_file": "Archivo",
        "contact_legend": "Contacto (opcional, sellado)",
        "contact_hint": (
            "Solo una persona responsable con permiso explícito puede verlo. Está "
            "cifrado, nunca se muestra públicamente y nunca revela quién aportó un "
            "registro."
        ),
        "label_name": "Nombre",
        "label_reach": "Cómo localizarle",
        "button_preview": "Previsualizar lo que ve un desconocido",
        "button_submit": "Enviar para revisión",
        # --- panel de vista previa ---
        "preview_heading": "Vista previa: lo que vería un desconocido",
        "preview_aria_label": "Lo que ve un desconocido",
        "preview_if_published": (
            "Si una persona responsable publica esto, un desconocido que no haya "
            "iniciado sesión vería:"
        ),
        "preview_content_warnings": "Advertencias de contenido: {list}",
        "preview_stranger_nothing_lead": "Un desconocido no ve nada.",
        "preview_stranger_nothing_detail": (
            " Publicado como {visibility}, este registro sería visible para {audience}: "
            "no aparecería en la exploración ni la búsqueda públicas."
        ),
        "preview_audience_community": "solo los miembros de la comunidad",
        "preview_audience_none": "nadie todavía",
        "preview_sealed_hint": (
            "Su nombre y su contacto nunca se muestran a ningún lector: están sellados. "
            "No están en esta vista previa."
        ),
        # --- confirmación de envío y retirada ---
        "thanks_heading": "Gracias: se recibió su contribución",
        "thanks_status": (
            "Está sellada y a la espera de que una persona responsable la revise. Nada "
            "de lo que envió es público todavía, y cualquier dato de contacto que haya "
            "proporcionado está cifrado y nunca se mostrará."
        ),
        "thanks_claim_heading": "Guarde esto por si cambia de opinión",
        "thanks_claim_intro": (
            "Mientras su envío siga a la espera de revisión, puede retirarlo usted "
            "mismo. Para ello necesitará ambos datos; manténgalos en privado, ya que "
            "juntos permiten retirar este envío:"
        ),
        "label_reference": "Referencia",
        "label_withdrawal_code": "Código de retirada",
        "thanks_withdraw_before": "Para retirarlo, vaya a",
        "withdrawal_page_link_text": "la página de retirada",
        "thanks_withdraw_after": "e introduzca ambos.",
        "thanks_edit_before": "También puede corregirlo en",
        "edit_page_link_text": "la página de edición",
        "withdraw_heading": "Retirar un envío",
        "withdraw_intro": (
            "Si aportó algo y todavía está a la espera de revisión, puede retirarlo "
            "aquí con la referencia y el código de retirada de su página de "
            "confirmación. Retirarlo elimina permanentemente el envío y borra cualquier "
            "dato de contacto que haya sellado con él. Una vez que una persona "
            "responsable haya publicado un registro, solicite un cambio desde su página."
        ),
        "withdraw_button": "Retirar este envío",
        # --- mensajes de rechazo / validación ---
        "err_title_required": "Se requiere un título.",
        "err_account_required": "Se requiere un relato.",
        "err_submission_too_long": "Ese envío es demasiado largo.",
        "err_contact_too_long": "Esos datos de contacto son demasiado largos.",
        "err_save_failed": (
            "No se pudo guardar su contribución en este momento. Inténtelo de nuevo."
        ),
        "err_file_too_large": "Ese archivo es demasiado grande. El límite es {max} MB.",
        "err_file_type": "Ese tipo de archivo no se acepta. Tipos permitidos: {types}.",
        "err_withdraw_failed": (
            "No se pudo retirar un envío pendiente con esa referencia y ese código. "
            "Compruebe ambos e inténtelo de nuevo."
        ),
        "err_edit_failed": (
            "No se encontró un envío pendiente con esa referencia y ese código. "
            "Compruebe ambos e inténtelo de nuevo."
        ),
        # --- editar un envío pendiente ---
        "edit_heading": "Editar un envío pendiente",
        "edit_intro": (
            "Si su envío todavía está a la espera de revisión, puede corregirlo aquí. "
            "Introduzca la referencia y el código de su confirmación, elija «Cargar mi "
            "envío», haga sus cambios y luego guárdelos. Una vez que una persona "
            "responsable haya publicado un registro, esto ya no se aplica: solicite un "
            "cambio desde su página."
        ),
        "label_code": "Su código",
        "edit_load_button": "Cargar mi envío",
        "edit_save_button": "Guardar cambios",
        "edit_done_heading": "Se guardaron sus cambios",
        "edit_done_status": (
            "Su envío pendiente se ha actualizado. Sigue sellado y a la espera de que "
            "una persona responsable lo revise."
        ),
        "withdraw_done_heading": "Su envío fue retirado",
        "withdraw_done_status": (
            "Se ha eliminado permanentemente, junto con cualquier dato de contacto que "
            "hubiera sellado con él. No queda nada de él en el archivo."
        ),
        "footer_privacy": (
            "Su identidad nunca se muestra. Los colaboradores controlan qué es público."
        ),
        "language_label": "Idioma",
        "visibility_public": "Público — cualquiera podrá leerlo cuando una persona responsable lo apruebe",
        "visibility_community": "Solo para la comunidad — miembros verificados de esta comunidad",
        "visibility_sealed": "Sellado — manténgalo reservado por ahora",
    },
}

# Plain-language glosses for the controlled content-warning vocabulary: language ->
# tag -> a one-line, non-clinical explanation of what the tag means, so a reader
# decides from understanding rather than from a bare jargon label (T9). The em dash
# separates the short label from its explanation. An unknown tag is humanized rather
# than looked up, so the catalog need not be exhaustive to stay safe.
_CW_GLOSSES: dict[str, dict[str, str]] = {
    "en": {
        # The default starter vocabulary (config._STARTER_CONTENT_WARNINGS), so every
        # tag a fresh archive ships with has a plain, native gloss at the decision point.
        "violence": "Violence — describes physical harm or attack",
        "sexual-violence": "Sexual violence — describes sexual assault or coercion",
        "abuse": "Abuse — describes ongoing harm or mistreatment",
        "self-harm": "Self-harm — describes a person hurting themselves",
        "suicide": "Suicide — describes suicide or suicidal thoughts",
        "medical": "Medical — describes injury, illness, or clinical procedures",
        "death": "Death — describes the death of a person",
        "incarceration": "Incarceration — describes jail, prison, or detention",
        "deportation": "Deportation — describes removal from a country or its threat",
        "outing": "Outing — reveals someone's identity without their consent",
        "hate-speech": "Hate speech — contains slurs or attacks on a group",
        "substance-use": "Substance use — describes drug or alcohol use",
        # Additional tags a steward may add to the vocabulary.
        "police-violence": "Police violence — describes harm or force by police",
        "deadnaming": "Deadnaming — refers to a name a person no longer uses",
    },
    "es": {
        "violence": "Violencia — describe daño físico o un ataque",
        "sexual-violence": "Violencia sexual — describe agresión o coerción sexual",
        "abuse": "Abuso — describe daño o maltrato continuo",
        "self-harm": "Autolesión — describe que una persona se hace daño a sí misma",
        "suicide": "Suicidio — describe el suicidio o pensamientos suicidas",
        "medical": "Médico — describe lesiones, enfermedades o procedimientos clínicos",
        "death": "Muerte — describe la muerte de una persona",
        "incarceration": "Encarcelamiento — describe cárcel, prisión o detención",
        "deportation": "Deportación — describe la expulsión de un país o su amenaza",
        "outing": "Exposición — revela la identidad de alguien sin su consentimiento",
        "hate-speech": "Discurso de odio — contiene insultos o ataques a un grupo",
        "substance-use": "Uso de sustancias — describe el uso de drogas o alcohol",
        "police-violence": "Violencia policial — describe daño o fuerza por parte de la policía",
        "deadnaming": "Nombre muerto — se refiere a un nombre que una persona ya no usa",
    },
}


def _humanize(tag: str) -> str:
    """Turn a raw tag like ``police-violence`` into ``Police violence``.

    Used as the safe fallback for an unknown content-warning tag and for any value
    the catalog does not gloss, so the UI shows a readable label rather than a slug.
    """
    return tag.replace("-", " ").replace("_", " ").strip().capitalize()


def _parse_accept_language(header: str) -> list[str]:
    """Parse an ``Accept-Language`` header into language tags, best preference first.

    Handles q-values (``en;q=0.8``), whitespace, and casing per RFC 9110. Entries
    with ``q=0`` are dropped (the client explicitly refuses them). A malformed or
    out-of-range q-value defaults that entry to the lowest acceptable weight rather
    than raising, so a bad header degrades to the default language instead of an
    error (robustness). Sorting is stable, so equal-q tags keep header order.
    """
    scored: list[tuple[int, float, str]] = []
    for index, raw_part in enumerate(header.split(",")):
        part = raw_part.strip()
        if not part:
            continue
        token, _, params = part.partition(";")
        tag = token.strip().lower()
        if not tag:
            continue
        quality = 1.0
        param = params.strip()
        if param.lower().startswith("q="):
            try:
                quality = float(param[2:].strip())
            except ValueError:
                quality = 0.0
        if not 0.0 < quality <= 1.0:
            # q=0 (or out of range) means "not acceptable" / unusable: drop it.
            continue
        # Negate quality and use the original index so a stable ascending sort
        # yields highest-quality first, ties broken by header order.
        scored.append((index, -quality, tag))
    scored.sort(key=lambda item: (item[1], item[0]))
    return [tag for _, _, tag in scored]


def negotiate(accept_language: str | None, available: Sequence[str] = SUPPORTED) -> str:
    """Pick the best available language for an ``Accept-Language`` header.

    Returns the highest-preference tag the client asked for that ``available`` can
    serve, matching either exactly (``es-MX`` -> ``es-MX``) or by primary subtag
    (``es-MX`` -> ``es``), case-insensitively. A ``*`` wildcard selects the first
    available language. If nothing matches — including ``None`` or an empty header —
    returns :data:`DEFAULT_LANG` (safety: a known-good fallback, never blank).
    """
    available_list = list(available)
    if not available_list:
        return DEFAULT_LANG
    # Map lower-cased available tags back to their canonical form for the result.
    canonical: dict[str, str] = {}
    for code in available_list:
        canonical.setdefault(code.lower(), code)
    primary_index: dict[str, str] = {}
    for code in available_list:
        primary_index.setdefault(code.lower().partition("-")[0], code)

    if accept_language is None:
        return DEFAULT_LANG
    for tag in _parse_accept_language(accept_language):
        if tag == "*":
            return available_list[0]
        if tag in canonical:
            return canonical[tag]
        primary = tag.partition("-")[0]
        if primary in primary_index:
            return primary_index[primary]
    return DEFAULT_LANG


def _lookup(lang: str, key: str) -> str | None:
    """Return the catalog template for ``(lang, key)``, falling back to English.

    Returns ``None`` only when the key is unknown in every language, so callers can
    distinguish "no such key" from "key exists, untranslated".
    """
    lang_table = _CATALOG.get(lang)
    if lang_table is not None and key in lang_table:
        return lang_table[key]
    default_table = _CATALOG.get(DEFAULT_LANG, {})
    return default_table.get(key)


def t(lang: str, key: str, /, **kw: object) -> str:
    """Look up a UI string by ``key`` in ``lang``, formatting it with ``kw``.

    Resolution order: the requested language, then English, then — if the key is
    unknown everywhere — the key itself. Formatting is forgiving: a template that
    references a placeholder not supplied in ``kw`` is returned unformatted rather
    than raising, so a catalog/caller mismatch can never take a page down. This
    function never raises (safety, robustness).
    """
    template = _lookup(lang, key)
    if template is None:
        return key
    if not kw:
        return template
    try:
        return template.format(**kw)
    except (KeyError, IndexError, ValueError):
        return template


def gloss_cw(lang: str, tag: str) -> str:
    """Return a plain-language gloss for a content-warning ``tag`` (user research T9).

    Resolution order: the requested language, then English, then a humanized form of
    the tag itself (hyphens/underscores to spaces, capitalized). This means a tag the
    catalog has never seen still renders as a readable label instead of a raw slug,
    so the controlled vocabulary can grow without code changes (robustness).
    """
    lang_glosses = _CW_GLOSSES.get(lang)
    if lang_glosses is not None and tag in lang_glosses:
        return lang_glosses[tag]
    default_glosses = _CW_GLOSSES.get(DEFAULT_LANG, {})
    if tag in default_glosses:
        return default_glosses[tag]
    return _humanize(tag)


def language_name(code: str) -> str:
    """Return the human-readable autonym for a language ``code``.

    ``en`` -> ``English``, ``es`` -> ``Español``. An unknown code matched only by its
    primary subtag (``es-MX`` -> ``Español``) is still named; a truly unknown code is
    returned humanized so a language picker never shows a blank entry.
    """
    if code in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[code]
    lowered = code.lower()
    if lowered in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[lowered]
    primary = lowered.partition("-")[0]
    if primary in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[primary]
    return _humanize(code)


def _catalog_view() -> Mapping[str, Mapping[str, str]]:
    """Return a read-only view of the catalog (for tests/introspection).

    Exposed so callers can verify coverage without importing the private dict; the
    returned mapping must not be mutated.
    """
    return _CATALOG
