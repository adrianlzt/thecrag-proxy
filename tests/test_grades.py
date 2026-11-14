import pytest

from app.grades import convert_grade_text, convert_html, convert_yds


@pytest.mark.parametrize(
    "yds,fr",
    [
        ("5.6", "4a"),
        ("5.8", "5a"),
        ("5.9", "5b+"),
        ("5.10a", "6a"),
        ("5.10b", "6a+"),
        ("5.10d", "6b+"),
        ("5.11d", "7a"),
        ("5.12a", "7a+"),
        ("5.13b", "8a"),
        ("5.14d", "9a"),
        ("5.15d", "9c"),
        ("5.10a/b", "6a/6a+"),
        ("5.10+", "6b"),
    ],
)
def test_yds(yds, fr):
    assert convert_yds(yds) == fr


def test_yds_no_toca_numeros_ni_rutas():
    s = "-83.65651 42.67m 5.10.3 /routes/with-grade/YDS:5.10a:5.10a width: 5.56%"
    assert convert_yds(s) == s


def test_yds_en_frase():
    assert convert_yds("5.10a [5.8 - 5.10a] ++") == "6a [5a - 6a] ++"
    assert convert_yds("crux de 5.9.") == "crux de 5b+."


@pytest.mark.parametrize("v,fb", [("V0", "4B+"), ("V3", "6A"), ("V6", "7A"), ("V10", "7C+"), ("V3-4", "6A/6B+")])
def test_v(v, fb):
    assert convert_grade_text(v) == fb


US_PAGE = """<html><head><title>5.7 ★★★Roadside Attraction | theCrag</title>
<meta property="og:title" content="5.7 ★★★Roadside Attraction" /></head><body>
<span class="grade gb2" title="Set using US grade config">5.7</span>
<a href="/area/1/routes/with-grade/YDS:5.10a:5.10a" title="<span class='gb3'>5.10a</span> 3 routes"><span>5.10a</span></a>
<div class="route" data-route-tick="{&quot;gradeAtom&quot;:{&quot;grade&quot;:&quot;5.9&quot;}}"></div>
<div class="phototopo" data-topodata='[{"id":1,"grade":"5.12a","class":"gb4"}]'></div>
<script>var x = "5.10a";</script>
</body></html>"""


def test_html_us():
    out = convert_html(US_PAGE)
    assert "<title>4b+ ★★★" in out
    assert 'content="4b+ ★★★' in out
    assert '<span class="grade gb2" title="Set using US grade config">4b+</span>' in out
    assert "YDS:5.10a:5.10a" in out  # los enlaces no se tocan
    assert "<span class='gb3'>6a</span> 3 routes" in out
    assert "<span>6a</span>" in out
    assert "&quot;grade&quot;:&quot;5.9&quot;" in out  # datos para apuntar ascensiones
    assert '"grade":"7a+"' in out
    assert 'var x = "5.10a";' in out


def test_html_no_us_intacto():
    page = '<title>Siurana</title><span class=" gb3">7a</span><p>5.5 km de aproximación</p>'
    assert convert_html(page) == page


def test_html_bloque():
    page = '<span class=" gb3">V4</span><a title="<span class=\'gb2\'>V0</span> 3"><span>V0</span></a>'
    assert convert_html(page) == '<span class=" gb3">6B+</span><a title="<span class=\'gb2\'>4B+</span> 3"><span>4B+</span></a>'
