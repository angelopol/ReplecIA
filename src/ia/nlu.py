"""NLU basada en un modelo multilingue preentrenado.

El objetivo es reducir reglas manuales al minimo:
- `normalize_text` limpia texto.
- `split_instructions` separa mensajes compuestos.
- `detect_intent` usa embeddings multilingues para clasificar intención.

Si no hay RAM suficiente para cargar el SentenceTransformer, se usa solo coincidencia léxica.
Si el modelo carga pero falla el batch de embeddings de intención, la intención pasa a léxica
y el emparejamiento de productos sigue usando embeddings (no se descarta el modelo entero).

Variables de entorno opcionales:
- `NLU_SKIP_EMBEDDINGS=1`: no cargar SentenceTransformer (útil en equipos con poca RAM).
- `NLU_PRODUCT_LEXICAL_ONLY=1`: aunque el modelo cargue para intención, el emparejamiento de **productos**
  usa solo texto del catálogo leído de la BD (`nombre_producto`, `descripcion`, `etiquetas`), sin embeddings.
- `NLU_MODEL_NAME`: permite cambiar el SentenceTransformer usado por la NLU.
- `HF_TOKEN`: token de Hugging Face para límites de descarga más altos (aviso sin token).

La lógica de sentimiento sigue separada en `sentiment_analyzer.py` con TextBlob.
"""

import logging
import os
import re
import unicodedata
from typing import Any, Dict, List

from ..core.product_tags import parse_etiquetas_list

_log = logging.getLogger(__name__)

from sentence_transformers import SentenceTransformer, util


def _env_flag_true(key: str) -> bool:
    return os.environ.get(key, "").strip().lower() in {"1", "true", "yes", "on"}


def _product_match_lexical_only() -> bool:
    """Productos solo con coincidencia léxica sobre datos del catálogo (SQLite), sin embeddings."""

    return _env_flag_true("NLU_PRODUCT_LEXICAL_ONLY")


MODEL_NAME = os.environ.get(
    "NLU_MODEL_NAME",
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
)

_st_model: SentenceTransformer | None = None
_st_model_disabled: bool = False
# Caché de embeddings de ejemplos de intención; el objeto _INTENT_EMBED_FAILED marca fallo del batch
# sin desactivar SentenceTransformer (el producto sigue usando el modelo).
_INTENT_EMBED_FAILED = object()
_intent_emb_cache: Any = None

INTENT_THRESHOLD = 0.35
PRODUCT_THRESHOLD = 0.60
PRODUCT_AMBIGUITY_MARGIN = 0.06

_PRODUCT_GENERIC_TOKENS = {
    "helado",
    "helados",
    "paleta",
    "paletas",
    "tina",
    "tinas",
    "producto",
    "productos",
    "pedido",
    "pedidos",
    "de",
    "del",
    "la",
    "el",
    "los",
    "las",
    "un",
    "una",
    "unos",
    "unas",
}

_PRODUCT_HINT_WORDS = {
    "helado",
    "helados",
    "paleta",
    "paletas",
    "tina",
    "tinas",
    "cono",
    "conos",
    "cones",
    "sabor",
    "sabores",
    "copa",
    "copas",
    "postre",
    "postres",
    # Coloquial (Venezuela / región); se normalizan también vía apply_colloquial_helado_terms
    "barquilla",
    "barquillas",
    "potecito",
    "potecitos",
    "pote",
    "potes",
    "litro",
    "litros",
}

_CATALOG_CUES = (
    "que hay",
    "que productos",
    "que helados",
    "catalogo",
    "menu",
    "lista de productos",
    "disponibles",
    "venden",
)

_PRICE_CUES = (
    "cuanto cuesta",
    "cuanto vale",
    "precio",
    "precios",
    "lista de precios",
    "cuanto cobran",
    "tarifa",
)

_ORDER_CUES = (
    "quiero",
    "necesito",
    "pedido",
    "comprar",
    "encargar",
    "dame",
    "traeme",
    "traerme",
    "para llevar",
    "a domicilio",
    "delivery",
    "domicilio",
    "envio a casa",
    "entrega a domicilio",
    "pickup",
    "pick up",
    "para buscar",
    "recoger",
    "recoger en tienda",
    "retirar",
    "retiro en tienda",
    "voy a buscar",
    "voy a buscarlo",
    "paso a buscar",
    "paso por",
    "voy a retirar",
)

_INTENT_EXAMPLES: dict[str, list[str]] = {
    "greeting": [
        "hola",
        "buenas",
        "buenos dias",
        "saludos",
        "que tal",
    ],
    "order": [
        "quiero 2 paletas",
        "necesito una tina de chocolate",
        "me puedes vender 3 helados",
        "quisiera hacer un pedido",
        "dame 6 unidades para delivery",
    ],
    "support": [
        "mi pedido no llego",
        "tengo un problema con mi pedido",
        "el producto llego dañado",
        "se me daño el pedido",
        "se me daño el pedido 12",
        "mi pedido llego roto",
        "mi pedido llego mal",
        "el producto llego roto",
        "no me responde nadie",
        "necesito soporte",
        "quiero reportar un reclamo",
    ],
    "help": [
        "como hago un pedido",
        "que puedo hacer aqui",
        "ayudame con las opciones",
        "explicame como funciona",
    ],
    "catalog": [
        "que helados tienen",
        "que hay en el menu",
        "mostrar catalogo",
        "que productos hay",
        "lista de productos",
        "que venden",
        "tienen paletas disponibles",
    ],
    "price": [
        "cuanto cuesta",
        "precio",
        "precio del producto",
        "cuanto vale",
        "precio de las paletas",
        "cuanto cobran por",
    ],
    "status": [
        "quiero saber el estado de mi pedido",
        "donde va mi pedido",
        "seguimiento del pedido",
        "como va el pedido 15",
    ],
    "cancel": [
        "quiero cancelar mi pedido",
        "anula el pedido",
        "anular pedido",
        "cancelar orden",
        "descartar pedido",
        "ya no lo quiero",
        "cancelar pedido 12",
    ],
}

_NUM_WORDS = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
}


def normalize_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.strip().lower()
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


# Formas coloquiales → términos más cercanos al catálogo típico (cono, tina, litro).
_COLOQUIAL_HELADO_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"\bbarquillas\b", "conos"),
    (r"\bbarquilla\b", "cono"),
    (r"\bcornettos\b", "conos"),
    (r"\bcornetto\b", "cono"),
    (r"\bpotecitos\b", "tinas"),
    (r"\bpotecito\b", "tina"),
    (r"\bpotes\b", "litros"),
    (r"\bpote\b", "litro"),
)


def apply_colloquial_helado_terms(normalized: str) -> str:
    """Reemplaza nombres regionales de presentaciones para acercar el texto al catálogo."""

    if not normalized:
        return ""
    s = normalized
    for pattern, repl in _COLOQUIAL_HELADO_REPLACEMENTS:
        s = re.sub(pattern, repl, s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s.strip())


def _catalog_known_keys(catalog: List[dict]) -> set[str]:
    """Claves y alias reconocibles de todos los productos (para detectar huecos / typos)."""

    out: set[str] = set()
    for p in catalog:
        n = p.get("nombre_producto")
        if not n:
            continue
        out.add(normalize_text(n))
        for a in _build_product_aliases(n):
            a = (a or "").strip()
            if len(a) < 3:
                continue
            out.add(a)
            out |= _spanish_token_variants(a)
        for a in _etiqueta_aliases_from_product(p):
            if len(a) < 2:
                continue
            out.add(a)
            if len(a) >= 3:
                out |= _spanish_token_variants(a)
        for a in _descripcion_aliases_from_product(p):
            if len(a) < 2:
                continue
            out.add(a)
            if len(a) >= 3:
                out |= _spanish_token_variants(a)
    return out


def _word_matches_catalog_known(word: str, known: set[str]) -> bool:
    if word in known:
        return True
    for v in _spanish_token_variants(word):
        if v in known:
            return True
    for k in known:
        if len(k) >= 4 and (k in word or word in k):
            return True
    return False


_CONTROL_COMMAND_STRIP_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"(?i)\b(?:cambi(?:ar|o|a))\s+(?:a|al)\s+(?:precio\s+)?(?:al\s+)?(?:mayor|mayorista|detal|menudeo)\b",
        r"(?i)\bquiero\s+(?:el\s+)?precio\s+(?:de\s+)?(?:mayorista|mayor|(?:al\s+)?detal|menudeo)\b",
        r"(?i)\b(?:pasar(?:me)?|pasame|pásame)\s+(?:a|al)\s+(?:mayor|mayorista|detal|menudeo)\b",
        r"(?i)\b(?:cerrar|finalizar|terminar)(?:\s+(?:el\s+)?(?:pedido|orden|carrito))?\b",
        r"(?i)\b(?:eso\s+es\s+todo|eso\s+seria\s+todo|eso\s+sería\s+todo)\b",
        r"(?i)\b(?:ya\s+no\s+quiero\s+mas|ya\s+no\s+quiero\s+más|no\s+quiero\s+mas|no\s+quiero\s+más)\b",
        r"(?i)\b(?:nada\s+mas|nada\s+más)\b",
        r"(?i)\b(?:ya\s+esta|ya\s+está)\b",
        r"(?i)\b(?:y\s+)?(?:recalcula(?:r)?|actualiza(?:r)?)\s+(?:el\s+)?carrito\b",
        r"(?i)\bquiero\s+(?:ordenar|pedir)\b",
        r"(?i)\b(?:continuar|seguir)\s+(?:con\s+)?(?:el\s+)?pedido\b",
        r"(?i)\bterminar\s+pedido\b",
    )
)


def strip_control_commands_for_product_search(text: str) -> str:
    """Quita frases de control (modo de precio, cierre de carrito) antes del fuzzy de producto."""

    raw = (text or "").strip()
    if not raw:
        return ""
    out = raw
    for pat in _CONTROL_COMMAND_STRIP_PATTERNS:
        out = pat.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def list_unknown_product_terms(text: str, catalog: List[dict]) -> List[str]:
    """Lista nombres o trozos que parecen producto pero no encajan con el catálogo.

    Incluye pares cantidad+palabra (p. ej. «20 mousie») y palabras sueltas mal escritas
    cuando el mensaje parece un pedido (orden o números).
    """

    if not text or not catalog:
        return []
    text = strip_control_commands_for_product_search(text)
    if not text:
        return []
    known = _catalog_known_keys(catalog)
    tn = apply_colloquial_helado_terms(normalize_text(text))
    parece_pedido = any(cue in tn for cue in _ORDER_CUES) or bool(re.search(r"\d", tn))
    skip_tokens = {
        normalize_text(w)
        for w in (
            "pedido",
            "orden",
            "carrito",
            "anexo",
            "anexa",
            "anexar",
            "agrega",
            "agregar",
            "para",
            "llevar",
            "delivery",
            "pickup",
            "tambien",
            "mas",
            "unas",
            "unos",
            "quiero",
            "dame",
            "necesito",
            "unidades",
            "unidad",
            "comprar",
            "encargar",
            "domicilio",
            "retiro",
            "recojo",
            "recoger",
            "efectivo",
            "transferencia",
            "detal",
            "mayor",
            "mayorista",
            "menudeo",
            "cambia",
            "cambiar",
            "cambio",
            "cerrar",
            "finalizar",
            "finaliza",
            "terminar",
            "termina",
            "catalogo",
            "menu",
            "gracias",
            "favor",
            "porfa",
            "litro",
            "litros",
        )
    }
    found: list[str] = []

    num_words = _NUM_WORDS
    number_token = r"(?:\d+|" + "|".join(re.escape(w) for w in num_words.keys()) + r")"
    pat_pair = re.compile(rf"(?<!\w)({number_token})\s+([\w]{{3,}})(?!\w)", re.IGNORECASE)
    pair_last_words: set[str] = set()
    for m in pat_pair.finditer(tn):
        word = normalize_text(m.group(2))
        if len(word) < 4 or word in skip_tokens:
            continue
        if _word_matches_catalog_known(word, known):
            continue
        found.append(f"{m.group(1)} {word}".strip())
        pair_last_words.add(word)

    if parece_pedido:
        for w in re.findall(r"\w+", tn):
            if len(w) < 4 or w in skip_tokens:
                continue
            if w in pair_last_words:
                continue
            if w in _PRODUCT_GENERIC_TOKENS:
                continue
            if _word_matches_catalog_known(w, known):
                continue
            if w in _PRODUCT_HINT_WORDS:
                continue
            found.append(w)

    return list(dict.fromkeys(found))


def split_instructions(text: str) -> List[str]:
    """Divide mensajes compuestos por puntuacion sencilla."""

    if not text:
        return []
    parts = re.split(r"[\n\.,;!?]", text)
    return [part.strip() for part in parts if part.strip()]


def _extract_quantity(text: str) -> int | None:
    """Extrae cantidad en numero o palabra comun."""

    normalized = normalize_text(text)
    match = re.search(r"\b(\d+)\b", normalized)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None

    for word, value in _NUM_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", normalized):
            return value
    return None


def _content_tokens(text: str) -> set[str]:
    """Devuelve tokens útiles para identificar un producto, excluyendo palabras genéricas."""

    return {
        token
        for token in re.findall(r"\w+", normalize_text(text))
        if len(token) > 2 and token not in _PRODUCT_GENERIC_TOKENS
    }


def _spanish_token_variants(token: str) -> set[str]:
    """Singular/plural simples (p. ej. cono/conos, paleta/paletas) para catálogo dinámico."""

    t = (token or "").strip().lower()
    if len(t) < 2:
        return set()
    out = {t}
    if len(t) >= 3:
        if t.endswith("s"):
            out.add(t[:-1])
        else:
            out.add(t + "s")
    return out


def _build_product_aliases(product_name: str) -> set[str]:
    """Genera formas alternativas de escribir un producto para mejorar el matching."""

    normalized = normalize_text(product_name)
    if not normalized:
        return set()

    tokens = [token for token in re.findall(r"\w+", normalized) if len(token) > 2]
    content_tokens = [token for token in tokens if token not in _PRODUCT_GENERIC_TOKENS]
    aliases = {normalized, " ".join(tokens), " ".join(content_tokens)}

    for token in tokens:
        if len(token) >= 3:
            aliases |= _spanish_token_variants(token)
    for token in content_tokens:
        if len(token) >= 3:
            aliases |= _spanish_token_variants(token)

    # Conserva también el nombre de la categoría para manejar pedidos como "una tina"
    # o "dos paletas", que suelen ser ambiguos y necesitan aclaración.
    for token in tokens:
        if token in {"helado", "helados", "paleta", "paletas", "tina", "tinas", "cono", "conos"}:
            aliases.add(token)
            if token.endswith("s"):
                aliases.add(token[:-1])

    if content_tokens:
        aliases.add(" ".join(reversed(content_tokens)))
        for hint in _PRODUCT_HINT_WORDS:
            aliases.add(f"{hint} {' '.join(content_tokens)}")
            aliases.add(f"{' '.join(content_tokens)} {hint}")
            aliases.add(f"{hint} de {' '.join(content_tokens)}")

    # Permite consultar por nombre parcial cuando el usuario omite la categoría.
    if len(content_tokens) > 1:
        aliases.add(" ".join(content_tokens[:-1]))
        aliases.add(" ".join(content_tokens[1:]))

    return {alias.strip() for alias in aliases if alias.strip()}


def _etiqueta_aliases_from_product(product: dict) -> set[str]:
    """Formas reconocibles a partir de etiquetas (JSON en catálogo); refuerza el matching junto al nombre."""

    raw = product.get("etiquetas")
    tags = raw if isinstance(raw, list) else parse_etiquetas_list(raw)
    out: set[str] = set()
    for tag in tags:
        nt = normalize_text(str(tag))
        if len(nt) < 2:
            continue
        out.add(nt)
        if len(nt) >= 3:
            out |= _spanish_token_variants(nt)
    return out


def _descripcion_aliases_from_product(product: dict) -> set[str]:
    """Tokens útiles desde la columna `descripcion` del producto (persistida en BD)."""

    desc = (product.get("descripcion") or "").strip()
    if not desc:
        return set()
    nt = normalize_text(desc)
    if not nt:
        return set()
    out: set[str] = set()
    for token in re.findall(r"\w+", nt):
        if len(token) < 3 or token in _PRODUCT_GENERIC_TOKENS:
            continue
        out.add(token)
        out |= _spanish_token_variants(token)
    return out


def _has_product_signal(text: str, catalog: List[dict]) -> bool:
    """Detecta si el texto realmente parece referirse a un producto del catalogo."""

    normalized = apply_colloquial_helado_terms(normalize_text(text))
    if not normalized or not catalog:
        return False

    if any(cue in normalized for cue in _ORDER_CUES):
        return True

    tokens = {token for token in re.findall(r"\w+", normalized) if len(token) > 2}
    if not tokens:
        return False

    for product in catalog:
        name = normalize_text(product.get("nombre_producto", ""))
        if not name:
            continue
        if name in normalized:
            return True
        product_tokens = {token for token in re.findall(r"\w+", name) if len(token) > 2}
        if tokens & product_tokens:
            return True
        for tag in _etiqueta_aliases_from_product(product):
            if len(tag) >= 3 and tag in normalized:
                return True
            if len(tag) > 2 and tag in tokens:
                return True
        for token in _descripcion_aliases_from_product(product):
            if len(token) >= 3 and token in normalized:
                return True
            if len(token) > 2 and token in tokens:
                return True

    return False


def _get_sentence_model() -> SentenceTransformer | None:
    """Carga perezosa del encoder; None si falla (memoria, red, etc.) o está desactivado."""

    global _st_model, _st_model_disabled
    if _st_model_disabled:
        return None
    if _st_model is not None:
        return _st_model
    if os.environ.get("NLU_SKIP_EMBEDDINGS", "").lower() in {"1", "true", "yes", "on"}:
        _log.info("NLU_SKIP_EMBEDDINGS activo: intención y producto solo por reglas léxicas.")
        _st_model_disabled = True
        return None
    try:
        _st_model = SentenceTransformer(MODEL_NAME)
    except (OSError, MemoryError, RuntimeError) as exc:
        _log.warning(
            "No se pudo cargar SentenceTransformer (memoria o disco; p. ej. archivo de paginación pequeño en Windows): %s. "
            "Usando NLU léxica. Opciones: ampliar memoria virtual, fijar NLU_SKIP_EMBEDDINGS=1 o HF_TOKEN para HF.",
            exc,
        )
        _st_model_disabled = True
        return None
    except Exception as exc:
        _log.warning("No se pudo cargar SentenceTransformer: %s", exc, exc_info=True)
        _st_model_disabled = True
        return None
    return _st_model


def _intent_examples_cache() -> tuple[list[str], Any] | None:
    global _intent_emb_cache
    if _intent_emb_cache is _INTENT_EMBED_FAILED:
        return None
    if _intent_emb_cache is not None:
        return _intent_emb_cache
    model = _get_sentence_model()
    if model is None:
        return None
    labels: list[str] = []
    examples: list[str] = []
    for intent, samples in _INTENT_EXAMPLES.items():
        labels.extend([intent] * len(samples))
        examples.extend(samples)
    try:
        embeddings = model.encode(
            examples,
            normalize_embeddings=True,
            convert_to_tensor=True,
            show_progress_bar=False,
        )
    except Exception as exc:
        _log.warning(
            "No se pudieron precomputar embeddings de intención (%s). "
            "Solo la intención usará reglas léxicas; el emparejamiento de productos sigue con el modelo.",
            exc,
        )
        _intent_emb_cache = _INTENT_EMBED_FAILED
        return None
    _intent_emb_cache = (labels, embeddings)
    return _intent_emb_cache


def _classify_intent_lexical(normalized: str) -> tuple[str, float]:
    """Clasificación por solapamiento con ejemplos, sin embeddings."""

    q_tokens = {t for t in re.findall(r"\w+", normalized) if len(t) > 1}
    if not q_tokens:
        return "unknown", 0.0
    best_intent = "unknown"
    best_agg = 0.0
    for intent, samples in _INTENT_EXAMPLES.items():
        best_local = 0.0
        for sample in samples:
            s = normalize_text(sample)
            if not s:
                continue
            if s in normalized or normalized in s:
                best_local = max(best_local, 0.82)
                continue
            s_tokens = {t for t in re.findall(r"\w+", s) if len(t) > 1}
            if not s_tokens:
                continue
            inter = len(q_tokens & s_tokens)
            uni = len(q_tokens | s_tokens)
            j = inter / uni if uni else 0.0
            best_local = max(best_local, j)
        if best_local > best_agg:
            best_agg = best_local
            best_intent = intent
    if best_agg < INTENT_THRESHOLD:
        return "unknown", best_agg
    return best_intent, best_agg


def _classify_intent(text: str) -> tuple[str, float]:
    normalized = normalize_text(text)
    if not normalized:
        return "unknown", 0.0

    cached = _intent_examples_cache()
    if cached is None:
        return _classify_intent_lexical(normalized)
    labels, embeddings = cached
    model = _get_sentence_model()
    if model is None:
        return _classify_intent_lexical(normalized)
    try:
        query = model.encode(
            normalized, normalize_embeddings=True, convert_to_tensor=True, show_progress_bar=False
        )
        scores = util.cos_sim(query, embeddings)[0]
    except Exception as exc:
        _log.warning("encode en clasificación de intención falló, usando léxico: %s", exc)
        return _classify_intent_lexical(normalized)

    best_scores: dict[str, float] = {}
    for index, label in enumerate(labels):
        score = float(scores[index].item())
        if score > best_scores.get(label, float("-inf")):
            best_scores[label] = score

    if not best_scores:
        return "unknown", 0.0

    best_intent, best_score = max(best_scores.items(), key=lambda item: item[1])
    if best_score < INTENT_THRESHOLD:
        return "unknown", best_score
    return best_intent, best_score


def _match_product_lexical_only(
    normalized: str,
    query_tokens: set[str],
    names: list[str],
    product_alias_map: dict[str, str],
    alias_names: list[str],
    alias_tokens: list[set[str]],
) -> tuple[str, float, list[str], float]:
    """Ranking por solapamiento de tokens cuando no hay SentenceTransformer."""

    if not alias_names:
        return "", 0.0, [], 0.0

    def _jaccard(i: int) -> float:
        union = query_tokens | alias_tokens[i]
        if not union:
            return 0.0
        return len(query_tokens & alias_tokens[i]) / len(union)

    ranking = sorted(
        (
            (index, len(query_tokens & alias_tokens[index]), _jaccard(index))
            for index in range(len(alias_names))
        ),
        key=lambda item: (item[1], item[2]),
        reverse=True,
    )
    candidates: list[str] = []
    seen_candidates: set[str] = set()
    for index, _, _ in ranking:
        candidate_name = product_alias_map[alias_names[index]]
        if candidate_name not in seen_candidates:
            seen_candidates.add(candidate_name)
            candidates.append(candidate_name)
        if len(candidates) >= 8:
            break

    best_index, best_overlap, best_score = ranking[0]
    best_alias = alias_names[best_index]
    best_name = product_alias_map[best_alias]
    second_score = ranking[1][2] if len(ranking) > 1 else 0.0
    confidence_gap = best_score - second_score

    if not query_tokens:
        cand = _refine_product_candidates(normalized, query_tokens, names, candidates)
        return "", best_score, cand, confidence_gap

    if best_overlap == 0 and best_score < 0.80:
        cand = _refine_product_candidates(normalized, query_tokens, names, candidates)
        return "", best_score, cand, confidence_gap
    if best_score < PRODUCT_THRESHOLD or confidence_gap < PRODUCT_AMBIGUITY_MARGIN:
        cand = _refine_product_candidates(normalized, query_tokens, names, candidates)
        return "", best_score, cand, confidence_gap
    cand = _refine_product_candidates(normalized, query_tokens, names, candidates)
    if re.search(r"(?<!\w)polet(?!\w)", normalized) and "polet" not in best_name.lower():
        return "", best_score, cand, confidence_gap
    return best_name, best_score, cand, confidence_gap


def _refine_product_candidates(
    normalized: str, query_tokens: set[str], catalog_names: list[str], candidates: list[str]
) -> list[str]:
    """Ajusta candidatos para marcas/palabras clave que deben acotar el listado (p. ej. Polet)."""

    if not candidates and not catalog_names:
        return candidates

    def _dedupe(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for name in seq:
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
        return out

    # Polet: si el usuario lo menciona explícitamente, solo mostrar líneas que contengan "polet".
    if re.search(r"(?<!\w)polet(?!\w)", normalized):
        polet_lines = sorted(
            [n for n in catalog_names if "polet" in n.lower()],
            key=lambda n: (len(n), n.lower()),
        )
        if polet_lines:
            return _dedupe(polet_lines)[:8]

    # Helado genérico: si solo dice "helado(s)" sin otra señal, sugerir presentaciones típicas del catálogo.
    if ("helado" in query_tokens or "helados" in query_tokens) and len(query_tokens) <= 3:
        cues = ("paleta", "tina", "cono", "litro", "napolitano", "sandwich")
        hinted = [n for n in catalog_names if any(c in n.lower() for c in cues)]
        if hinted:
            hinted.sort(key=lambda n: n.lower())
            return _dedupe(hinted)[:8]

    return _dedupe(candidates)[:8]


def _match_product(normalized_query: str, catalog: List[dict]) -> tuple[str, float, list[str], float]:
    """Busca el producto mas cercano semánticamente al texto del cliente.

    `normalized_query` debe ser el resultado de normalize_text + apply_colloquial_helado_terms.
    """

    if not catalog:
        return "", 0.0, [], 0.0

    names = [product["nombre_producto"] for product in catalog if product.get("nombre_producto")]
    if not names:
        return "", 0.0, [], 0.0

    normalized = normalized_query.strip()
    query_tokens = _content_tokens(normalized)
    product_alias_map: dict[str, str] = {}
    alias_names: list[str] = []
    alias_tokens: list[set[str]] = []
    for product in catalog:
        name = product.get("nombre_producto")
        if not name:
            continue
        for alias in _build_product_aliases(name):
            if alias not in product_alias_map:
                product_alias_map[alias] = name
                alias_names.append(alias)
                alias_tokens.append(_content_tokens(alias))
    for product in catalog:
        name = product.get("nombre_producto")
        if not name:
            continue
        for alias in _etiqueta_aliases_from_product(product):
            if len(alias) < 3:
                continue
            if alias not in product_alias_map:
                product_alias_map[alias] = name
                alias_names.append(alias)
                alias_tokens.append(_content_tokens(alias))
        for alias in _descripcion_aliases_from_product(product):
            if len(alias) < 3:
                continue
            if alias not in product_alias_map:
                product_alias_map[alias] = name
                alias_names.append(alias)
                alias_tokens.append(_content_tokens(alias))

    # Prioriza coincidencias de frase completa dentro del texto, para manejar
    # frases naturales como "quiero 2 crema ponche" o "pago movil, quiero helado chocolate".
    phrase_candidates = []
    for alias, product_name in product_alias_map.items():
        if len(alias) < 4:
            continue
        pattern = rf"(?<!\w){re.escape(alias)}(?!\w)"
        if re.search(pattern, normalized):
            phrase_candidates.append((len(alias), product_name, alias))

    if phrase_candidates:
        phrase_candidates.sort(reverse=True)
        _, matched_name, matched_alias = phrase_candidates[0]
        other_names = [name for name in names if name != matched_name][:4]
        cand = [matched_name] + other_names
        cand = _refine_product_candidates(normalized, query_tokens, names, cand)
        return matched_name, 1.0, cand, 1.0

    if normalized in product_alias_map:
        matched_name = product_alias_map[normalized]
        cand = [matched_name] + [name for name in names if name != matched_name][:4]
        cand = _refine_product_candidates(normalized, query_tokens, names, cand)
        return matched_name, 1.0, cand, 1.0

    model = _get_sentence_model()
    if model is None or _product_match_lexical_only():
        name, score, cand, gap = _match_product_lexical_only(
            normalized, query_tokens, names, product_alias_map, alias_names, alias_tokens
        )
        cand = _refine_product_candidates(normalized, query_tokens, names, cand)
        return name, score, cand, gap

    try:
        query = model.encode(
            normalized, normalize_embeddings=True, convert_to_tensor=True, show_progress_bar=False
        )
        embeddings = model.encode(
            alias_names, normalize_embeddings=True, convert_to_tensor=True, show_progress_bar=False
        )
        scores = util.cos_sim(query, embeddings)[0]
    except Exception as exc:
        _log.warning("encode en match de producto falló, usando léxico: %s", exc)
        name, score, cand, gap = _match_product_lexical_only(
            normalized, query_tokens, names, product_alias_map, alias_names, alias_tokens
        )
        cand = _refine_product_candidates(normalized, query_tokens, names, cand)
        return name, score, cand, gap

    ranking = sorted(
        (
            (
                index,
                len(query_tokens & alias_tokens[index]),
                float(scores[index].item()),
            )
            for index in range(len(alias_names))
        ),
        key=lambda item: (item[1], item[2]),
        reverse=True,
    )
    candidates = []
    seen_candidates: set[str] = set()
    for index, _, _ in ranking:
        candidate_name = product_alias_map[alias_names[index]]
        if candidate_name not in seen_candidates:
            seen_candidates.add(candidate_name)
            candidates.append(candidate_name)
        if len(candidates) >= 12:
            break

    best_index, best_overlap, best_score = ranking[0]
    best_alias = alias_names[best_index]
    best_name = product_alias_map[best_alias]
    second_score = ranking[1][2] if len(ranking) > 1 else 0.0
    confidence_gap = best_score - second_score

    # If the user only wrote a generic term like "paleta" or "helado", we keep
    # the result ambiguous unless there is an exact alias match.
    if not query_tokens:
        cand = _refine_product_candidates(normalized, query_tokens, names, candidates)
        return "", best_score, cand, confidence_gap

    # Require a lexical signal beyond generic terms, or a very strong semantic match.
    if best_overlap == 0 and best_score < 0.80:
        cand = _refine_product_candidates(normalized, query_tokens, names, candidates)
        return "", best_score, cand, confidence_gap
    if best_score < PRODUCT_THRESHOLD or confidence_gap < PRODUCT_AMBIGUITY_MARGIN:
        cand = _refine_product_candidates(normalized, query_tokens, names, candidates)
        return "", best_score, cand, confidence_gap
    cand = _refine_product_candidates(normalized, query_tokens, names, candidates)
    if re.search(r"(?<!\w)polet(?!\w)", normalized) and "polet" not in best_name.lower():
        return "", best_score, cand, confidence_gap
    return best_name, best_score, cand, confidence_gap


def detect_intent(text: str, catalog: List[dict] | None = None) -> Dict[str, Any]:
    """Clasifica intención y extrae entidades basicas."""

    text_prod = strip_control_commands_for_product_search(text)
    normalized = normalize_text(text)
    norm_pedido = apply_colloquial_helado_terms(normalize_text(text_prod))
    intent, intent_score = _classify_intent(normalized)
    entities: Dict[str, Any] = {"intent_score": intent_score}

    quantity = _extract_quantity(normalized)
    if quantity is not None:
        entities["quantity"] = quantity

    if any(cue in normalized for cue in _PRICE_CUES):
        if intent in {"unknown", "catalog", "help", "greeting"}:
            intent = "price"
    elif intent == "unknown" and any(cue in normalized for cue in _CATALOG_CUES):
        intent = "catalog"

    # If there is a catalog available and the message contains product-like signals,
    # try to match products and expose clarification data to the handler.
    if catalog and (intent == "order" or quantity is not None or _has_product_signal(text_prod, catalog)):
        product, score, candidates, confidence_gap = _match_product(norm_pedido, catalog)
        if candidates:
            entities["product_candidates"] = candidates
            entities["product_clarify"] = product == ""
            entities["product_confidence_gap"] = confidence_gap
        if product:
            entities["product"] = product
            entities["product_match_score"] = score
            entities["product_clarify"] = False
            # Convert product mentions to order unless the user is clearly asking
            # for catalog/price information.
            if intent in {"unknown", "greeting", "help", "catalog", "price"}:
                has_order_cue = any(cue in normalized for cue in _ORDER_CUES)
                has_catalog_cue = any(cue in normalized for cue in _CATALOG_CUES)
                has_price_cue = any(cue in normalized for cue in _PRICE_CUES)
                if quantity is not None or has_order_cue or (not has_catalog_cue and not has_price_cue):
                    intent = "order"
                elif intent == "unknown":
                    intent = "catalog"

    if (
        entities.get("product")
        and entities.get("quantity") is None
        and not re.search(r"\b\d+\b", norm_pedido)
        and not any(re.search(rf"\b{re.escape(w)}\b", norm_pedido) for w in _NUM_WORDS)
    ):
        entities.setdefault("missing_fields", []).append("quantity")

    return {"intent": intent, "entities": entities, "text": text}