"""Made-up responses in each site's JSON shape, for the parser tests. Every id, name and number here is invented;
nothing is copied from a real account, session or video."""

T0 = 1_791_200_000  # an arbitrary timestamp (2026-10-05)


def tiktok_search() -> list[dict]:
    return [{"status_code": 0, "data": [
        {"type": 1, "item": {"id": "1000000000000000001", "desc": "test video one #tagone #tagtwo", "createTime": T0,
                             "author": {"uniqueId": "Creator_A"}, "authorStats": {"followerCount": 1200},
                             "stats": {"playCount": 5000, "diggCount": 400, "commentCount": 30, "shareCount": 20,
                                       "collectCount": 10},
                             "video": {"duration": 14, "cover": "https://cover.invalid/1.jpg"},
                             "music": {"id": "m1", "title": "test sound"}}},
        {"type": 4, "user_list": []}],
        "item_list": [{"id": "1000000000000000002", "desc": "test video two", "createTime": T0 - 86400,
                       "author": {"uniqueId": "creator_b"}, "statsV2": {"playCount": "700", "diggCount": "40"},
                       "video": {"duration": 9}}]}]


def instagram_search() -> list[dict]:
    return [{"media_grid": {"sections": [{"layout_content": {"medias": [
        {"media": {"code": "TESTREEL001", "media_type": 2, "product_type": "clips", "taken_at": T0, "video_duration": 21.5,
                   "play_count": 8000, "like_count": 600, "comment_count": 12, "caption": {"text": "test reel #tagone"},
                   "user": {"username": "Creator_C"},
                   "image_versions2": {"candidates": [{"url": "https://cover.invalid/r.jpg"}]}}},
        {"media": {"code": "TESTPHOTO01", "media_type": 1, "taken_at": T0, "like_count": 5}}]}}]}}]


def x_search() -> list[dict]:
    def post(sid: str, text: str, video: bool) -> dict:
        legacy = {"id_str": sid, "full_text": text, "created_at": "Sun Oct 05 12:00:00 +0000 2026",
                  "favorite_count": 300, "reply_count": 10, "retweet_count": 40, "quote_count": 5, "bookmark_count": 8}
        if video:
            legacy["extended_entities"] = {"media": [{"type": "video", "media_url_https": "https://cover.invalid/x.jpg",
                                                      "video_info": {"duration_millis": 17000}}]}
        return {"content": {"itemContent": {"tweet_results": {"result": {
            "__typename": "Tweet", "rest_id": sid, "views": {"count": "9000"}, "legacy": legacy,
            "core": {"user_results": {"result": {"core": {"screen_name": "Creator_D"},
                                                 "legacy": {"followers_count": 500}}}}}}}}}

    entries = [post("1000000000000000003", "test post #tagone https://t.co/abc", True),
               post("1000000000000000004", "text only post", False)]
    return [{"data": {"search_by_raw_query": {"search_timeline": {"timeline": {"instructions": [
        {"type": "TimelineAddEntries", "entries": entries}]}}}}}]


def trend_hashtags() -> list[dict]:
    curve = lambda *v: [{"timestamp": str(T0 + i * 86400), "value": x} for i, x in enumerate(v)]  # noqa: E731
    return [{"items": [
        {"hashtagName": "TagOne", "rankIndex": "1", "publishCnt": "5000", "vv": "900000", "popularityCurve": curve(0, 100, 60)},
        {"hashtagName": "tagtwo", "rankIndex": "2", "publishCnt": "3000", "vv": "500000", "popularityCurve": curve(10, 60, 95)},
        {"hashtagName": "tagthree", "rankIndex": "3", "publishCnt": "1000", "vv": "90000", "popularityCurve": curve(50, 52, 50)}],
        "pagination": {"hasMore": False}}]


def trend_videos(organic_share: float = 1.0, title: str = "test trend video #tagone") -> list[dict]:
    def entity(i: int) -> dict:
        views = 100_000 * (5 - i)
        return {"itemInfo": {"itemID": f"100000000000000001{i}", "title": title, "createTime": str(T0),
                             "coverURL": "https://cover.invalid/v.jpg"},
                "itemAuthorInfo": {"handlerName": f"creator_{i}"}, "itemAuthorMetrics": {"followers": "2000"},
                "itemMetrics": {"videoViews": str(views), "organicVideoViews": str(int(views * organic_share))}}

    return [{"entityInfos": [entity(i) for i in range(1, 5)], "pagination": {"hasMore": True}}]


def ytdlp_info() -> dict[str, dict]:
    """What yt-dlp's extract_info returns, trimmed to the fields we read (invented values)."""
    return {
        "tiktok": {"id": "1000000000000000021", "extractor_key": "TikTok",
                   "webpage_url": "https://www.tiktok.com/@creator_e/video/1000000000000000021",
                   "uploader": "creator_e", "uploader_id": "2000000000000000001", "channel": "Creator E",
                   "channel_follower_count": None, "title": "Test clip with a deadpan dance...",
                   "description": "Test clip with a deadpan dance, all made up. #Halloween #creator_e", "tags": None,
                   "view_count": 3731, "like_count": 222, "comment_count": 5, "repost_count": 4, "save_count": 9,
                   "duration": 8, "timestamp": 1762009942, "track": "", "width": 1080, "height": 1920,
                   "thumbnail": "https://cover.invalid/t.jpg"},
        "youtube": {"id": "TestShort01", "extractor_key": "Youtube",
                    "webpage_url": "https://www.youtube.com/watch?v=TestShort01", "uploader": "Creator F",
                    "uploader_id": "@creator_f", "channel": "Creator F", "channel_follower_count": 18800,
                    "title": "#dance #cute", "description": "", "tags": [], "view_count": 3933, "like_count": 180,
                    "comment_count": 5, "repost_count": None, "save_count": None, "duration": 17,
                    "timestamp": 1790165704, "track": None, "width": 720, "height": 1280,
                    "thumbnail": "https://cover.invalid/y.jpg"},
    }



def instagram_profile_reels() -> list[dict]:
    """A profile's Reels grid: no taken_at (the date is in the media id), with Instagram's AI label."""
    return [{"data": {"xdt_api__v1__clips__user__connection_v2": {"edges": [
        {"node": {"media": {"pk": "4002616216279166823", "code": "TESTREEL101", "product_type": "clips", "media_type": 2,
                            "play_count": 2414000, "like_count": 49000, "comment_count": 30,
                            "user": {"username": "Creator_H"}, "ai_label_info": {"ai_label_type": "AI_GENERATED"}}}},
        {"node": {"media": {"pk": "4002616216279166824", "code": "TESTREEL102", "product_type": "clips", "media_type": 2,
                            "play_count": 900, "like_count": 4, "user": {"username": "creator_h"}, "ai_label_info": {}}}}]}}}]
