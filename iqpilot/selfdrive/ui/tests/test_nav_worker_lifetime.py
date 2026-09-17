# Copyright (c) 2026 IQ.Lvbs. All rights reserved.
import threading
import time
from types import SimpleNamespace

import pytest

from iqpilot.ui.onroad import nav_map_panel as nav


@pytest.mark.parametrize('provider_class', [nav.MapboxTileProvider, nav.OsmOfflineProvider])
def test_release_discards_images_decoded_after_map_is_hidden(provider_class, tmp_path, monkeypatch):
  monkeypatch.setattr(nav, 'TILE_CACHE_ROOT', tmp_path)
  provider = provider_class()
  tile = (15, 0, 0)
  started = threading.Event()
  finish = threading.Event()
  images = []
  released = []
  original_unload = nav.rl.unload_image

  def decode(payload):
    image = nav.rl.gen_image_color(128, 128, nav.rl.WHITE)
    images.append(image)
    started.set()
    assert finish.wait(5)
    return image

  def unload(image):
    released.append(image)
    original_unload(image)

  monkeypatch.setattr(nav, '_decode_tile_image', decode)
  monkeypatch.setattr(nav.rl, 'unload_image', unload)
  monkeypatch.setattr(provider, '_visible_tile_keys', lambda *args: (15, 1, 0, 0, [tile]))
  if provider_class is nav.MapboxTileProvider:
    monkeypatch.setattr(provider, '_token', lambda: '')
    path = provider._cache_path(tile)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'tile')
  else:
    monkeypatch.setattr(provider, '_refresh_source', lambda *args: None)
    monkeypatch.setattr(provider, '_load_blob', lambda *args: b'tile')
    monkeypatch.setattr(provider, '_source_tile_for_request', lambda value: (value, 0))
    provider._status = 'offline_ready'

  try:
    provider.update(0, 0, 15, 100, 100)
    assert started.wait(5)
    provider.release()
    finish.set()
    deadline = time.monotonic() + 5
    while provider._inflight and time.monotonic() < deadline:
      time.sleep(.001)
    assert not provider._inflight
    assert not provider._pending_tiles
    assert len(images) == len(released) == 1
  finally:
    finish.set()
    provider.release()


def test_texture_pool_bounds_all_dimensions_and_reuses_matching_textures(monkeypatch):
  released = []
  updated = []
  monkeypatch.setattr(nav.rl, 'unload_texture', lambda texture: released.append(texture.id))
  monkeypatch.setattr(nav.rl, 'update_texture', lambda texture, data: updated.append((texture.id, data)))
  pool = nav.TexturePool(3)
  for index in range(100):
    pool.release(SimpleNamespace(id=index + 1, width=128 + index, height=128))
  assert sum(map(len, pool._free.values())) == 3
  assert len(pool._free) <= 3
  assert len(released) == 97
  width, height = next(iter(pool._free))
  texture = pool.acquire(SimpleNamespace(width=width, height=height, data=b'pixels'))
  assert updated == [(texture.id, b'pixels')]
  assert sum(map(len, pool._free.values())) == 2
  pool.release(texture)
  pool.drain()
  assert not pool._free
  assert len(released) == len(set(released)) == 100


def test_day_mode_change_keeps_old_response_out_of_new_style(tmp_path, monkeypatch):
  monkeypatch.setattr(nav, 'TILE_CACHE_ROOT', tmp_path)
  provider = nav.MapboxTileProvider()
  started = threading.Event()
  finish = threading.Event()
  tile = (15, 1, 2)
  old_path = provider._cache_path(tile)
  response = SimpleNamespace(status_code=200, content=b'tile', raise_for_status=lambda: None)

  def get(*args, **kwargs):
    started.set()
    assert finish.wait(5)
    return response

  monkeypatch.setattr(provider._session, 'get', get)
  monkeypatch.setattr(nav, '_decode_tile_image', lambda payload: nav.rl.gen_image_color(128, 128, nav.rl.WHITE))
  worker = threading.Thread(target=provider._fetch_worker, args=(tile, 'token', provider._generation))
  worker.start()
  try:
    assert started.wait(5)
    provider.set_day_mode(True)
    finish.set()
    worker.join(5)
    assert not worker.is_alive()
    assert old_path.read_bytes() == b'tile'
    assert not provider._cache_path(tile).exists()
    assert provider._style == 'navigation-day-v1'
    assert not provider._pending_tiles
    assert provider._status == 'idle'
  finally:
    finish.set()
    worker.join(5)
    provider.release()
