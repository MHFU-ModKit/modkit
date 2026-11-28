-- SPDX-License-Identifier: MIT
-- SPDX-FileCopyrightText: 2026 sp00ktober
---@meta

---The fixture API.
---@class fx
---@field count integer how many
---@field label? string optional label
fx = {}

---The version.
fx.api_version = 3

---The item limit.
fx.MAX = 8

---The modes.
fx.MODE = {
  A = 1,
  B = 2,
}

---Adds a to b.
---
---```lua
---local n = fx.add(1)
---```
---@param a integer first operand
---@param b? integer second operand
---@return integer sum the sum
function fx.add(a, b) end

---Both coordinates.
---@return integer x
---@return integer y
function fx.pair() end

function fx.bare(v) end

---Options of a call.
---@class fx.Opts
---@field n integer
---@field name? string the name
