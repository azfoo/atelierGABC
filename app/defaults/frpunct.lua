--[[
   File `frpunct.lua’ derived from babel-french
         [2018/02/08 v1.01]
   Copyright © 2018 Daniel Flipo
   <daniel (dot) flipo (at) free (dot) fr>
   License LPPL1.3.
--]]

FBsp = {}
-- Espaces fines devant : ; ! ? changer en {1, 0, 0} pour espace-mot.
FBsp.thin  = {.4, 0, 0}
FBsp.colon = {.4, 0, 0}
local FB_punct_thin =
  {[string.byte("!")] = true,
   [string.byte("?")] = true,
   [string.byte(";")] = true}
local FB_punct_thick =
  {[string.byte(":")] = true}
local FB_punct_left =
  {[string.byte("!")] = true,
   [string.byte("?")] = true,
   [string.byte(";")] = true,
   [string.byte(":")] = true,
  }
local new_node     = node.new
local copy_node    = node.copy
local node_id      = node.id
local GLUE         = node_id("glue")
local GLYPH        = node_id("glyph")
local PENALTY      = node_id("penalty")
local nobreak      = new_node(PENALTY)
nobreak.penalty    = 10000
local insert_node_before = node.insert_before
local remove_node        = node.remove
local font_table = {}
local function new_glue_scaled (fid,table)
  if fid > 0 and table[1] then
     local fp = font_table[fid]
     if not fp then
        local ft = font.getfont(fid)
        if ft then
           font_table[fid] = ft.parameters
           fp = font_table[fid]
        end
     end
     local gl = new_node(GLUE,0)
     if fp then
        node.setglue(gl, table[1]*fp.space,
                         table[2]*fp.space_stretch,
                         table[3]*fp.space_shrink)
        return gl
     else
        return nil
     end
  else
     return nil
  end
end
local function french_punctuation (head)
  for item in node.traverse_id(GLYPH, head) do
    local char = item.char
    local fid  = item.font
    local nbspace  = new_node("glyph")
    if FB_punct_left[char] and fid > 0 then
       local prev = item.prev
       local prev_id, prev_subtype, prev_char
       if prev then
          prev_id = prev.id
          prev_subtype = prev.subtype
          if prev_id == GLYPH then
             prev_char = prev.char
          end
       end
       local is_glue = prev_id == GLUE
       local glue_wd
       if is_glue then
          glue_wd = prev.width
       end
       local realglue = is_glue and glue_wd > 1
       local t
       if FB_punct_thick[char] then
          t = FBsp.colon
       else
          t = FBsp.thin
       end
       local fbglue = new_glue_scaled(fid, t)
       if fbglue then
          if realglue then
             head = remove_node(head,prev,true)
          end
          insert_node_before(head, item, copy_node(nobreak))
          insert_node_before(head, item, copy_node(fbglue))
       end
    end
  end
  return head
end
return french_punctuation
--  End of File frpunct.lua.
