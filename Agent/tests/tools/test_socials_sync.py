"""Plan 9: syncing our channels into snapshots (fake readers, the test DB, made-up numbers)."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from tf_agent.credentials import Credentials
from tf_agent.socials.sync import SocialSync
from tf_agent.socials.types import ChannelRead, PostRead, ReadError
from tf_agent.testing import create_test_run
from tf_db.models import SocialChannel, SocialChannelSnapshot, SocialPost, SocialSnapshot

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
REEL = "https://www.instagram.com/reel/TESTOWN001/"


def channel_read(views=100, source="api", posts=None):
    return ChannelRead(handle="made_up_channel", followers=140, posts_count=1, source=source, posts=posts if posts
                       is not None else [PostRead(platform_post_id="17900000000000001" if source == "api" else
                                                  "TESTOWN001", url=REEL, posted_at=NOW - timedelta(days=1),
                                                  views=views, likes=10, reach=80 if source == "api" else None)])


class FakeApi:
    def __init__(self, reads=None, error=None):
        self.reads, self.error, self.refreshed = list(reads or []), error, 0

    async def read(self, token, since):
        if self.error:
            raise ReadError(self.error, f"made-up {self.error}")
        return self.reads.pop(0)

    async def refresh(self, *args):
        self.refreshed += 1
        return {"access_token": "refreshed-made-up", "expires_in": 5_184_000, "refresh_token": "made-up-refresh"}


class FakePublic:
    def __init__(self, read=None, error=False):
        self.result, self.error, self.calls = read, error, 0

    async def read(self, platform, handle, since):
        self.calls += 1
        if self.error:
            raise ReadError("unavailable", "the public page didn't load")
        return self.result


async def setup(sm, tmp_path, mode="api", platform="instagram", expires_in_days=50.0):
    char_id, _, _ = await create_test_run(sm, n_tasks=0)
    async with sm() as s:
        ch = SocialChannel(character_id=char_id, platform=platform, handle="made_up_channel", mode=mode)
        s.add(ch)
        await s.commit()
    creds = Credentials(tmp_path / "secrets")
    if mode == "api":
        creds.save_social_token(str(ch.id), {"access_token": "made-up-token", "refresh_token": "made-up-refresh",
                                             "expires_at": (NOW + timedelta(days=expires_in_days)).timestamp()})
    return ch.id, creds


def sync(sm, creds, api, public, now=NOW):
    return SocialSync(sm, creds, instagram=api, tiktok=api, public=public, clock=lambda: now)


async def rows(sm, model):
    async with sm() as s:
        return (await s.execute(select(model))).scalars().all()


async def test_each_sync_adds_one_snapshot_per_post_and_one_for_the_channel(db_sessionmaker, tmp_path):
    cid, creds = await setup(db_sessionmaker, tmp_path)
    api = FakeApi([channel_read(100), channel_read(150), channel_read(400)])
    first = await sync(db_sessionmaker, creds, api, FakePublic()).sync_channel(cid)
    await sync(db_sessionmaker, creds, api, FakePublic(), NOW + timedelta(minutes=10)).sync_channel(cid)  # too soon
    await sync(db_sessionmaker, creds, api, FakePublic(), NOW + timedelta(hours=3)).sync_channel(cid)
    assert (first.posts, first.new_posts, first.error, first.source) == (1, 1, None, "api")
    (post,) = await rows(db_sessionmaker, SocialPost)
    assert post.platform_post_id == "TESTOWN001" and post.canonical_id == "instagram:TESTOWN001"
    assert sorted(s.views for s in await rows(db_sessionmaker, SocialSnapshot)) == [150, 400]  # the soon one updated
    assert len(await rows(db_sessionmaker, SocialChannelSnapshot)) == 2


async def test_an_expired_token_says_reconnect_and_the_public_numbers_still_come_in(db_sessionmaker, tmp_path):
    cid, creds = await setup(db_sessionmaker, tmp_path)
    public = FakePublic(channel_read(90, source="public"))
    out = await sync(db_sessionmaker, creds, FakeApi(error="expired"), public).sync_channel(cid)
    assert out.source == "public" and out.error.startswith("reconnect: ")
    (ch,) = await rows(db_sessionmaker, SocialChannel)
    assert ch.mode == "api" and ch.last_error.startswith("reconnect: ") and ch.last_synced_at == NOW
    (snap,) = await rows(db_sessionmaker, SocialSnapshot)
    assert (snap.views, snap.reach) == (90, None)  # public pages give no reach: unknown, not 0


async def test_the_same_post_from_the_api_and_the_public_page_is_one_post(db_sessionmaker, tmp_path):
    cid, creds = await setup(db_sessionmaker, tmp_path)
    await sync(db_sessionmaker, creds, FakeApi([channel_read(100)]), FakePublic()).sync_channel(cid)
    await sync(db_sessionmaker, creds, FakeApi(error="revoked"), FakePublic(channel_read(120, source="public")),
               NOW + timedelta(hours=3)).sync_channel(cid)
    assert len(await rows(db_sessionmaker, SocialPost)) == 1 and len(await rows(db_sessionmaker, SocialSnapshot)) == 2


async def test_a_token_close_to_expiry_is_refreshed_first(db_sessionmaker, tmp_path):
    cid, creds = await setup(db_sessionmaker, tmp_path, expires_in_days=2)
    api = FakeApi([channel_read()])
    await sync(db_sessionmaker, creds, api, FakePublic()).sync_channel(cid)
    assert api.refreshed == 1 and creds.social_token(str(cid))["access_token"] == "refreshed-made-up"


async def test_a_channel_with_no_posts_yet_and_a_failing_one_never_stop_the_rest(db_sessionmaker, tmp_path):
    empty_id, creds = await setup(db_sessionmaker, tmp_path, mode="public")
    out = await sync(db_sessionmaker, creds, FakeApi(), FakePublic(channel_read(posts=[], source="public"))
                     ).sync_channel(empty_id)
    assert (out.posts, out.error) == (0, None)
    async with db_sessionmaker() as s:
        s.add(SocialChannel(character_id=(await s.get(SocialChannel, empty_id)).character_id, platform="tiktok",
                            handle="made_up_other", mode="public"))
        await s.commit()
    results = await sync(db_sessionmaker, creds, FakeApi(), FakePublic(error=True)).sync_all()
    assert set(results) == {"made_up_channel", "made_up_other"}
    assert all(r.error == "the public page didn't load" for r in results.values())



async def test_a_renamed_account_brings_its_new_username_with_the_official_api(db_sessionmaker, tmp_path):
    cid, creds = await setup(db_sessionmaker, tmp_path)
    renamed = channel_read()
    renamed.handle = "Made.Up.Channel"
    await sync(db_sessionmaker, creds, FakeApi([renamed]), FakePublic()).sync_channel(cid)
    (ch,) = await rows(db_sessionmaker, SocialChannel)
    assert ch.handle == "made.up.channel"
    public = channel_read(source="public")
    public.handle = "someone_else"  # a public read only echoes the handle it was given: never renames
    await sync(db_sessionmaker, creds, FakeApi(error="expired"), FakePublic(public),
               NOW + timedelta(hours=3)).sync_channel(cid)
    assert (await rows(db_sessionmaker, SocialChannel))[0].handle == "made.up.channel"
