-- SPDX-License-Identifier: MIT
-- SPDX-FileCopyrightText: 2026 sp00ktober
-- Handles over the flat mhfu bindings, run before every mod: mhfu.mem, mhfu.world, mhfu.entity
-- (Entity), mhfu.MON and mhfu.AREA. Annotations stay whole `---` lines: the embedded copy blanks
-- them (Makefile).

---@class mhfu
local M = mhfu

---The raw memory bindings under short names; valid is mem_valid.
M.mem = {
  read_u8   = M.read_u8,   read_u16 = M.read_u16,  read_u32 = M.read_u32,
  write_u8  = M.write_u8,  write_u16= M.write_u16, write_u32= M.write_u32,
  read_f32  = M.read_f32,  write_f32= M.write_f32, valid    = M.mem_valid,
}

---A handle, an {x, y, z} or {x=, y=, z=} table.
---@alias mhfu.Point mhfu.Entity|mhfu.Player|table

---@param a mhfu.Point|number
---@param b? number
---@param c? number
---@return number x, number y, number z
local function to_xyz(a, b, c)
  if type(a) == "table" then
    if a.pos then return a:pos() end
    if a.x then return a.x, a.y, a.z end
    return a[1], a[2], a[3]
  end
  ---@cast a number
  ---@cast b number
  ---@cast c number
  return a, b, c
end

---A handle over an entity pointer: a method named like an mhfu.entity_* binding is that binding
---on ptr, and setters return the handle.
---@class mhfu.Entity
---@field ptr integer
local Entity = {}
Entity.__index = Entity

---A handle over ptr.
---@param ptr integer
---@return mhfu.Entity
function M.entity_wrap(ptr) return setmetatable({ ptr = ptr }, Entity) end
---Entity handles: wrap is mhfu.entity_wrap.
M.entity = M.entity or {}
M.entity.wrap = M.entity_wrap

---True while ptr is nonzero and in the registry.
---@return boolean
function Entity:valid()      return self.ptr ~= 0 and M.entity_alive(self.ptr) end
function Entity:type()       return M.entity_type(self.ptr) end
function Entity:hp()         return M.entity_hp(self.ptr) end
function Entity:size()       return M.entity_size(self.ptr) end
---@param v number
function Entity:set_size(v)  M.entity_set_size(self.ptr, v); return self end
function Entity:pos()        return M.entity_pos(self.ptr) end
---@param a mhfu.Point|number
---@param b? number
---@param c? number
function Entity:set_pos(a,b,c) local x,y,z = to_xyz(a,b,c); M.entity_set_pos(self.ptr,x,y,z); return self end
function Entity:yaw()        return M.entity_yaw(self.ptr) end
---@param v integer
function Entity:set_yaw(v)   M.entity_set_yaw(self.ptr, v); return self end
function Entity:ai_state()   return M.entity_ai_state(self.ptr) end
---@param v integer
function Entity:set_ai_state(v) M.entity_set_ai_state(self.ptr, v); return self end
function Entity:engaged()    return M.entity_engaged(self.ptr) end
---@param b boolean
function Entity:set_engaged(b) M.entity_set_engaged(self.ptr, b); return self end
function Entity:section()    return M.entity_section(self.ptr) end
---@param v integer
function Entity:set_section(v) M.entity_set_section(self.ptr, v); return self end
function Entity:calm()       M.entity_calm(self.ptr); return self end

---A same-species clone as a new handle, or nil (mhfu.entity_clone).
---@return mhfu.Entity?
function Entity:clone()
  local p = M.entity_clone(self.ptr)
  if not p or p == 0 then return nil end
  return M.entity_wrap(p)
end

---mhfu.entity_make_visible, in the player's area by default.
---@param section? integer
function Entity:make_visible(section)
  M.entity_make_visible(self.ptr, section or M.get_area_index()); return self
end

---XZ distance to target.
---@param target mhfu.Point
---@return number
function Entity:dist_to(target)
  local tx, _, tz = to_xyz(target)
  local mx, _, mz = self:pos()
  local dx, dz = tx - mx, tz - mz
  return math.sqrt(dx*dx + dz*dz)
end

---mhfu.entity_force_aggro on target's position.
---@param target mhfu.Point
function Entity:force_aggro(target)
  local tx, ty, tz = to_xyz(target)
  M.entity_force_aggro(self.ptr, tx, ty, tz); return self
end

---Moves offset units (default 0) past target along +X, then make_visible().
---@param target mhfu.Point
---@param offset? number
function Entity:teleport_near(target, offset)
  local tx, ty, tz = to_xyz(target)
  self:set_pos(tx + (offset or 0), ty, tz)
  self:make_visible()
  return self
end

---The hunter, from mhfu.world.player(); pos is mhfu.player_pos.
---@class mhfu.Player
local Player = {}
Player.__index = Player
---@type mhfu.Player
local PLAYER = setmetatable({}, Player)

function Player:pos()          return M.player_pos() end
function Player:hp()           return M.get_player_hp() end
function Player:area()         return M.get_area_index() end
function Player:screen_state() return M.get_screen_state() end
---XZ distance to ent.
---@param ent mhfu.Point
---@return number
function Player:dist_to(ent)
  local tx, _, tz = to_xyz(self)
  local mx, _, mz = to_xyz(ent)
  local dx, dz = tx - mx, tz - mz
  return math.sqrt(dx*dx + dz*dz)
end

---World state: the player, screen, area, quest clock and entity lookup.
M.world = {
  player        = function() return PLAYER end,
  screen_state  = M.get_screen_state,
  area          = M.get_area_index,
  quest_timer   = M.get_quest_timer,
  ---True in a quest area (screen state 17).
  in_area       = function() return M.get_screen_state() == 17 end,
  paint_map     = M.paint_map,
}

---Every live entity of a monster type, as handles.
---@param type integer
---@return mhfu.Entity[]
function M.world.entities(type)
  local out = {}
  for _, p in ipairs(M.entities_of_type(type)) do out[#out+1] = M.entity_wrap(p) end
  return out
end
---The first live entity of a monster type, or nil.
---@param type integer
---@return mhfu.Entity?
function M.world.first(type)
  local list = M.entities_of_type(type)
  if list and list[1] and list[1] ~= 0 then return M.entity_wrap(list[1]) end
  return nil
end

---Monster ids by name: the mhfu.MON_* constants.
M.MON = {
  ANTEKA = M.MON_ANTEKA, POPO = M.MON_POPO,
  TIGREX = M.MON_TIGREX, GIADROME = M.MON_GIADROME,
}
---Snowy-mountains area indices, as get_area_index and entity_section give them; an index names
---the map as well as the section.
M.AREA = { BASECAMP = 98, SNOW_S1 = 99, SNOW_S6 = 100 }

M.log("[prelude] OO layer ready (mhfu.world / mhfu.entity / mhfu.mem / mhfu.MON / mhfu.AREA)")
