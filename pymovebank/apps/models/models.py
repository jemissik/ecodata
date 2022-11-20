import param
import panel as pn

from pymovebank.apps.models import config


class PMVCard(pn.Card):
    active_header_background = param.String(
        default=config.ACCENT,
        doc="""
        A valid CSS color for the header background when not collapsed.""",
    )

    header_background = param.String(
        default=config.ACCENT,
        doc="""
        A valid CSS color for the header background.""",
    )

    header_color = param.String(
        default="white",
        doc="""
        A valid CSS color to apply to the header text.""",
    )

    header_css_classes = param.List(
        ["card-header-custom"],
        doc="""CSS classes to apply to the header element.""")


class PMVCardDark(pn.Card):
    active_header_background = param.String(
        default=config.PALETTE[7],
        doc="""
        A valid CSS color for the header background when not collapsed.""",
    )

    header_background = param.String(
        default=config.PALETTE[7],
        doc="""
        A valid CSS color for the header background.""",
    )

    header_color = param.String(
        default="white",
        doc="""
        A valid CSS color to apply to the header text.""",
    )

    header_css_classes = param.List(
        ["card-header-custom"],
        doc="""CSS classes to apply to the header element.""")

