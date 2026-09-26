"""An event card's picture: never a dead link on a post that can't fix itself."""

from modules.event_images import usable_image

BROKEN = (
    "https://cdn.leekduck.com/assets/img/events/article-images/2026/"
    "2026-09-26-pokemon-go-city-safari-rio-de-janeiro-2026/safarizone-default.jpg"
)
ROOT_DEFAULT = "https://cdn.leekduck.com/assets/img/events/safarizone-default.jpg"


def statuses(table):
    async def status(url):
        value = table.get(url, 200)
        if isinstance(value, Exception):
            raise value
        return value

    return status


async def test_a_working_image_is_kept():
    assert (
        await usable_image("https://x.test/a.jpg", statuses({}))
        == "https://x.test/a.jpg"
    )


async def test_a_misfiled_default_falls_back_to_the_shared_one():
    # 2026-09-26: ScrapedDuck named the placeholder under the article folder,
    # where it 404s; the same file lives in the events folder itself.
    status = statuses({BROKEN: 404, ROOT_DEFAULT: 200})
    assert await usable_image(BROKEN, status) == ROOT_DEFAULT


async def test_a_dead_image_with_no_fallback_is_dropped():
    status = statuses({"https://x.test/gone.jpg": 404})
    assert await usable_image("https://x.test/gone.jpg", status) is None


async def test_a_network_hiccup_keeps_the_image():
    status = statuses({"https://x.test/a.jpg": TimeoutError()})
    assert await usable_image("https://x.test/a.jpg", status) == "https://x.test/a.jpg"


async def test_no_image_stays_none():
    assert await usable_image(None, statuses({})) is None
