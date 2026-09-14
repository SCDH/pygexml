# mypy: disallow_untyped_defs=False
# mypy: disallow_untyped_calls=False

import string

import hypothesis.strategies as st

from pygexml.geometry import Point, Box, Polygon
from pygexml.image import Image
from pygexml.page import Coords, Page, TextLine, TextRegion

st_points = st.builds(Point, x=st.integers(min_value=0), y=st.integers(min_value=0))


@st.composite
def st_box_points(draw):
    tl = draw(st_points)
    br_x = draw(st.integers(min_value=tl.x + 1))
    br_y = draw(st.integers(min_value=tl.y + 1))
    br = Point(x=br_x, y=br_y)
    return tl, br


st_boxes = st.builds(
    lambda pp: Box(top_left=pp[0], bottom_right=pp[1]), st_box_points()
)

st_polygons = st.builds(Polygon, st.lists(st_points, min_size=1))
st_polygons2 = st.builds(Polygon, st.lists(st_points, min_size=2))

st_coords = st.builds(Coords, polygon=st_polygons2)

st_coords_strings = st.builds(str, st_coords)


def st_xml_text(**kwargs):
    xml_chars = (
        st.characters(min_codepoint=0x20, max_codepoint=0xD7FF)
        | st.characters(min_codepoint=0xE000, max_codepoint=0xFFFD)
        | st.characters(min_codepoint=0x10000, max_codepoint=0x10FFFF)
        | st.sampled_from(["\t", "\n"])
    )
    return st.text(alphabet=xml_chars, **kwargs)


def st_simple_text(**kwargs):
    simple = string.ascii_letters + string.digits + " _-"
    return st.text(alphabet=simple, max_size=20, **kwargs)


st_text_lines = st.builds(
    TextLine,
    id=st_simple_text(),
    coords=st_coords,
    text=st_xml_text(),
    confidence=st.one_of(st.none(), st.floats(min_value=0, max_value=1)),
)

st_text_regions = st.builds(
    TextRegion,
    id=st_simple_text(),
    coords=st_coords,
    textlines=st.builds(
        lambda lines: {l.id: l for l in lines}, st.lists(st_text_lines)
    ),
)

st_images = st.builds(
    Image,
    filename=st_simple_text(),
    width=st.one_of(st.none(), st.integers(min_value=1)),
    height=st.one_of(st.none(), st.integers(min_value=1)),
)

st_images_with_dimensions = st.builds(
    Image,
    filename=st_simple_text(),
    width=st.integers(min_value=1),
    height=st.integers(min_value=1),
)


@st.composite
def st_pages(draw):
    image = draw(st_images)
    regions = {tr.id: tr for tr in draw(st.lists(st_text_regions))}
    reading_order = draw(st.one_of(st.none(), st.permutations(list(regions.keys()))))
    return Page(image=image, regions=regions, reading_order=reading_order)


@st.composite
def st_pages_with_dimensions(draw):
    image = draw(st_images_with_dimensions)
    regions = {tr.id: tr for tr in draw(st.lists(st_text_regions))}
    reading_order = draw(st.one_of(st.none(), st.permutations(list(regions.keys()))))
    return Page(image=image, regions=regions, reading_order=reading_order)
