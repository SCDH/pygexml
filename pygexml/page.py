from pathlib import Path
from re import Pattern, compile
from warnings import warn
from dataclasses import dataclass, field
from dataclasses_json import DataClassJsonMixin
from typing import ClassVar, TypeAlias
from collections.abc import Iterable, Mapping
from lxml import etree
from lxml.etree import _Element as Element, QName

from .geometry import Point, Box, Polygon, GeometryError
from .image import Image


def find_child(element: Element, name: str) -> Element | None:
    for child in element:
        if QName(child).localname == name:
            return child
    return None


def find_children(element: Element, name: str) -> Iterable[Element]:
    return (child for child in element if QName(child).localname == name)


def _parse_alto_coords(element: Element) -> "Coords":
    box_attrs = ["HPOS", "VPOS", "WIDTH", "HEIGHT"]
    if not all(attr in element.attrib for attr in box_attrs):
        raise ALTOXMLError("Missing one of the box attributes")
    return Coords.from_box(
        Box.from_top_left_width_height(
            top_left=Point(
                x=int(element.attrib["HPOS"]), y=int(element.attrib["VPOS"])
            ),
            width=int(element.attrib["WIDTH"]),
            height=int(element.attrib["HEIGHT"]),
        )
    )


class PageXMLError(Exception):
    pass


class ALTOXMLError(Exception):
    pass


@dataclass
class Coords(DataClassJsonMixin):
    polygon: Polygon

    # Loose regex that allows for negative values that can be handled by
    # our code. Context: PeroOCR sometimes produces PageXML with negative
    # coordinate values.
    # https://github.com/DCGM/pero-ocr/issues/84#issuecomment-3745059403
    LOOSE_PATTERN: ClassVar[Pattern[str]] = compile(
        r"^(-?[0-9]+,-?[0-9]+ )+(-?[0-9]+,-?[0-9]+)$"
    )

    # Regex from official Page-XML spec
    STRICT_PATTERN: ClassVar[Pattern[str]] = compile(
        r"^([0-9]+,[0-9]+ )+([0-9]+,[0-9]+)$"
    )

    def __post_init__(self) -> None:
        if len(self.polygon.points) < 2:
            raise PageXMLError("At least 2 Points are required")

    @classmethod
    def parse(cls, points_str: str) -> "Coords":

        if not cls.LOOSE_PATTERN.match(points_str):
            raise PageXMLError("Invalid Coords XML string")

        if not cls.STRICT_PATTERN.match(points_str):
            warn(
                "Warning: Coords XML string does not match the PAGE XMl spec: "
                + points_str
            )

        points: list[Point] = []
        for pair_str in points_str.split(" "):
            [x, y] = pair_str.split(",")
            points.append(Point(x=int(x), y=int(y)))

        try:
            polygon = Polygon(points=points)
        except GeometryError:
            raise PageXMLError("At least 2 Points are required")

        return Coords(polygon=polygon)

    @classmethod
    def from_box(cls, box: Box) -> "Coords":
        return cls(polygon=Polygon.from_box(box))

    def __str__(self) -> str:
        return " ".join(str(p) for p in self.polygon.points)


ID: TypeAlias = str


@dataclass
class LayoutLine(DataClassJsonMixin):
    id: ID
    coords: Coords


@dataclass
class LayoutRegion(DataClassJsonMixin):
    id: ID
    coords: Coords
    textlines: Mapping[ID, LayoutLine]


@dataclass
class PageLayout(DataClassJsonMixin):
    image: Image
    regions: Mapping[ID, LayoutRegion]


@dataclass
class TextLine(LayoutLine, DataClassJsonMixin):
    text: str

    @classmethod
    def from_xml(cls, element: Element) -> "TextLine":
        if QName(element).localname != "TextLine":
            raise PageXMLError("Wrong element given")
        if "id" not in element.attrib:
            raise PageXMLError("No id found")
        coords_element = find_child(element, "Coords")
        if coords_element is None:
            raise PageXMLError("No Coords found")
        if "points" not in coords_element.attrib:
            raise PageXMLError("Coords has no points attribute")
        text_equiv = min(
            find_children(element, "TextEquiv"),
            key=lambda te: int(te.attrib.get("index", 0)),
            default=None,
        )
        text_element = (
            find_child(text_equiv, "Unicode") if text_equiv is not None else None
        )
        if text_element is None:
            raise PageXMLError("No text found")
        return TextLine(
            id=str(element.attrib["id"]),
            coords=Coords.parse(str(coords_element.attrib["points"])),
            text=text_element.text if text_element.text is not None else "",
        )

    @classmethod
    def from_alto(cls, element: Element) -> "TextLine":
        if QName(element).localname != "TextLine":
            raise ALTOXMLError("Wrong element given")
        if "ID" not in element.attrib:
            raise ALTOXMLError("No ID found")

        coords = _parse_alto_coords(element)

        if len(element) == 0:
            raise ALTOXMLError("No text elements found")

        text: str = ""
        for child in element:
            match QName(child).localname:
                case "String":
                    if "CONTENT" in child.attrib:
                        text += str(child.attrib["CONTENT"])
                case "SP":
                    text += " "

        return TextLine(id=str(element.attrib["ID"]), coords=coords, text=text)

    def words(self) -> Iterable[str]:
        return self.text.split()


@dataclass
class TextRegion(LayoutRegion, DataClassJsonMixin):
    textlines: Mapping[ID, TextLine]  # pyright: ignore[reportIncompatibleVariableOverride]  # fmt: skip

    @classmethod
    def from_xml(cls, element: Element) -> "TextRegion":
        if QName(element).localname != "TextRegion":
            raise PageXMLError("Wrong element given")
        if "id" not in element.attrib:
            raise PageXMLError("No id found")
        coords_element = find_child(element, "Coords")
        if coords_element is None:
            raise PageXMLError("No Coords element found")
        if "points" not in coords_element.attrib:
            raise PageXMLError("Coords has no points attribute")
        text_lines = find_children(element, "TextLine")

        return TextRegion(
            id=str(element.attrib["id"]),
            coords=Coords.parse(str(coords_element.attrib["points"])),
            textlines={
                tl.id: tl for tl in (TextLine.from_xml(tl) for tl in text_lines)
            },
        )

    @classmethod
    def from_alto(cls, element: Element) -> "TextRegion":
        if QName(element).localname != "TextBlock":
            raise ALTOXMLError("Wrong element given")
        if "ID" not in element.attrib:
            raise ALTOXMLError("No ID found")

        coords = _parse_alto_coords(element)

        textlines: dict[ID, TextLine] = {}
        for child in element:
            if QName(child).localname == "TextLine":
                tl = TextLine.from_alto(child)
                textlines[tl.id] = tl

        if not textlines:
            raise ALTOXMLError("No TextLine elements found")

        return TextRegion(
            id=str(element.attrib["ID"]), coords=coords, textlines=textlines
        )

    def lookup_textline(self, id: ID) -> TextLine | None:
        return self.textlines.get(id)

    def all_text(self) -> Iterable[str]:
        return (tl.text for tl in self.textlines.values())

    def all_words(self) -> Iterable[str]:
        return (w for tl in self.textlines.values() for w in tl.words())


def _parse_reading_order_group(element: Element) -> list[ID]:
    children = list(element)
    if QName(element).localname in ("OrderedGroup", "OrderedGroupIndexed"):
        children.sort(key=lambda c: int(c.attrib.get("index", 0)))
    result: list[ID] = []
    for child in children:
        name = QName(child).localname
        if name in ("RegionRef", "RegionRefIndexed") and "regionRef" in child.attrib:
            result.append(
                str(child.attrib["regionRef"])
            )  # silently skip malformed entries without regionRef
        elif "Group" in name:
            result.extend(_parse_reading_order_group(child))
    return result


@dataclass
class Page(PageLayout, DataClassJsonMixin):
    regions: Mapping[ID, TextRegion]  # pyright: ignore[reportIncompatibleVariableOverride]  # fmt: skip
    reading_order: list[ID] | None = field(default=None)

    @classmethod
    def from_xml(cls, element: Element) -> "Page":
        if QName(element).localname != "Page":
            raise PageXMLError("Wrong element given")

        if "imageFilename" not in element.attrib:
            raise PageXMLError("No image filename found")

        regions = find_children(element, "TextRegion")

        reading_order_element = find_child(element, "ReadingOrder")
        reading_order: list[ID] | None = None
        if reading_order_element is not None:
            for child in reading_order_element:
                if "Group" in QName(child).localname:
                    reading_order = _parse_reading_order_group(child)
                    break

        return Page(
            image=Image(
                filename=str(element.attrib["imageFilename"]),
                width=(
                    int(element.attrib["imageWidth"])
                    if "imageWidth" in element.attrib
                    else None
                ),
                height=(
                    int(element.attrib["imageHeight"])
                    if "imageHeight" in element.attrib
                    else None
                ),
            ),
            regions={
                tr.id: tr for tr in (TextRegion.from_xml(region) for region in regions)
            },
            reading_order=reading_order,
        )

    @classmethod
    def from_xml_string(cls, xml_str: str) -> "Page":
        root = etree.fromstring(xml_str.encode("utf-8"))
        page_element = find_child(root, "Page")
        if page_element is None:
            raise PageXMLError("No page element found")
        return cls.from_xml(page_element)

    @classmethod
    def from_xml_file(cls, file: Path | str, encoding: str = "utf-8") -> "Page":
        path = Path(file)
        xml_string = path.read_text(encoding=encoding)
        return Page.from_xml_string(xml_string)

    @classmethod
    def from_alto(cls, element: Element) -> "Page":
        if QName(element).localname != "alto":
            raise ALTOXMLError("Wrong element given")

        image_element = find_child(element, "Description")
        if image_element is None:
            raise ALTOXMLError("No Description element found")
        image_element = find_child(image_element, "sourceImageInformation")
        if image_element is None:
            raise ALTOXMLError("No sourceImageInformation element found")
        filename_element = find_child(image_element, "fileName")
        if filename_element is None:
            raise ALTOXMLError("No fileName element found")
        image_filename = (
            filename_element.text if filename_element.text is not None else ""
        )

        layout = find_child(element, "Layout")
        if layout is None:
            raise ALTOXMLError("No Layout element found")
        page_element = find_child(layout, "Page")
        if page_element is None:
            raise ALTOXMLError("No Page element found")
        printspace_element = find_child(page_element, "PrintSpace")
        if printspace_element is None:
            raise ALTOXMLError("No PrintSpace element found")

        text_blocks = find_children(printspace_element, "TextBlock")

        # ALTO allows for float values, but we convert to int for consistency with PAGE XML
        image_width = (
            int(float(page_element.attrib["WIDTH"]))
            if "WIDTH" in page_element.attrib
            else None
        )
        image_height = (
            int(float(page_element.attrib["HEIGHT"]))
            if "HEIGHT" in page_element.attrib
            else None
        )

        return Page(
            image=Image(
                filename=image_filename, width=image_width, height=image_height
            ),
            regions={
                tb.id: tb for tb in (TextRegion.from_alto(tb) for tb in text_blocks)
            },
        )

    @classmethod
    def from_alto_string(cls, xml_str: str) -> "Page":
        root = etree.fromstring(xml_str.encode("utf-8"))
        return cls.from_alto(root)

    @classmethod
    def from_alto_file(cls, file: Path | str, encoding: str = "utf-8") -> "Page":
        path = Path(file)
        xml_string = path.read_text(encoding=encoding)
        return Page.from_alto_string(xml_string)

    def lookup_region(self, id: ID) -> TextRegion | None:
        return self.regions.get(id)

    def regions_ordered(self) -> list[TextRegion]:
        if self.reading_order is None:
            return list(self.regions.values())
        ordered = [
            self.regions[rid] for rid in self.reading_order if rid in self.regions
        ]
        seen_ids = {r.id for r in ordered}
        rest = [r for r in self.regions.values() if r.id not in seen_ids]
        return ordered + rest

    def all_text(self) -> Iterable[str]:
        return (line for region in self.regions_ordered() for line in region.all_text())

    def all_words(self) -> Iterable[str]:
        return (
            word for region in self.regions_ordered() for word in region.all_words()
        )
