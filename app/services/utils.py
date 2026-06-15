from __future__ import annotations

import unicodedata


STOPWORDS = frozenset("""a ante ao aos após até com contra de desde em entre
para perante por sem sob sobre trás o a os as da das do dos dum duns
num nums numa um uma umas uns ele ela eles elas me te se nos vos
lhe lhes eu tu você vocês o a os as meu minha meus minhas teu tua
teus tuas seu sua seus suas nosso nossa nossos nossas isso isto esse
essa esses esses estas este esta estes estas aquele aquela aquelas aquilo
que qual quem como quanto quanta quantos quantas onde aonde donde
quando porque porquê pois já também ainda muito pouco mais menos
demais todo toda todos todas algum alguma alguns algumas nenhum nenhuma
nenhuns nenhumas certo certa certos certas outro outra outros outras
vário vária vários várias tanto tanta tantos quantos quanto quanta
quantos qualquer quaisquer cada qual seja seja se caso sim não nem
era são fora fosse fosse fossem fosseis fosseis temos têm tem havia
haja hajam hajas hajamos hajais haja são seja seja sejamos sejais
sejam seria seriam seria seriam será serão seria seriam era eram é
são está estão estava estavam esteve estivera estiveram estivera
esteve estiveram estiverem estejamos estejais esteja estejam esteja
fui foi fomos foram fora foram fosse fosse fossem fosseis fosseis
fosse fosse fossem fora foram irei irá irão iria iriam iria iriam
vá vão vamos vais vai vou vai vai vão vamos""".split())


def remove_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def clean_tags(tags_str: str) -> list[str]:
    return [t.strip().lower()[:20] for t in tags_str.split(",") if t.strip()]


def clean_tag_list(raw_tags: list) -> list[str]:
    cleaned = [str(t).strip().lower()[:20] for t in raw_tags if t and str(t).strip()]
    return cleaned[:5]


def extract_keywords(text: str) -> str:
    plain = remove_accents(text)
    words = plain.lower().split()
    seen = set()
    keywords = []
    for w in words:
        w = w.strip(""".,;:!?()[]{}"'""")
        if len(w) > 2 and w not in STOPWORDS and not w.startswith(("http", "www")) and w not in seen:
            seen.add(w)
            keywords.append(w)
    return " ".join(keywords[:10])
