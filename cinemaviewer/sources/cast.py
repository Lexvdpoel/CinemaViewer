"""Displaycast as a source: the sidebar entry, and stream addresses for what phones cast."""

from ..cast import ytdl
from ..models import MediaItem


class CastSource:
    key = "cast"
    label = "Displaycast"

    def libraries(self):
        return [MediaItem(source=self.key, id="displaycast", title="Displaycast", kind="displaycast",
                          group="Displaycast", subtitle="YouTube en DLNA vanaf je telefoon")]

    def children(self, item):
        return []

    def play_url(self, item):
        """Runs off the UI thread. For YouTube the details (title, channel, length) come with the stream."""
        if item.extra.get("cast") == "youtube":
            info = ytdl.resolve(item.extra["video_id"])
            item.title = info["title"] or item.title
            item.subtitle = " · ".join(x for x in (info["channel"], "YouTube") if x)
            item.overview = info.get("description", "")
            item.runtime_ms = int(info.get("duration") or 0) * 1000
            if info.get("thumbnail"):
                item.image_url = info["thumbnail"]
            options = []
            if info.get("audio"):
                options.append(":input-slave=" + info["audio"])  # high quality: picture and sound are separate
            agent = (info.get("headers") or {}).get("User-Agent")
            if agent:
                options.append(":http-user-agent=" + agent)
            item.extra["vlc_options"] = options
            return info["video"]
        if item.kind == "photo":
            item.extra["vlc_options"] = [":image-duration=-1"]  # a photo stays until something else is sent
        return item.extra["mrl"]

    def markers(self, item):
        return {"credits": None, "chapters": []}

    def report_start(self, item, pos_ms):
        pass

    def report_progress(self, item, pos_ms, paused):
        pass

    def report_stop(self, item, pos_ms):
        pass
