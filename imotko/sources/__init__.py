"""Източници на обяви."""

from imotko.sources.base import ListingSource
from imotko.sources.imot_bg import ImotBg

# източникът по подразбиране; други сайтове се добавят тук
source: ListingSource = ImotBg()
