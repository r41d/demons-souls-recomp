-- Demon's Souls co-op (work in progress, docs/COOP_DEV.md): the event-script module. tools/make_coop.py appends it
-- to global_event.lua in every map's script bundle of game_coop/. It redefines the game's own multiplayer handlers:
-- the goal is the "seamless co-op" of the later games' mods -- the player who joins is an equal player, stays through
-- boss fights, and a boss killed together counts in both saves.
-- Everything it does prints a "[coop]" line (DES_LUA_PRINT=1 shows them in the log).

-- Step A: the guest is a player in body form, not a blue phantom. The join itself is still the game's white-phantom
-- summon (the only way in); once the guest has loaded into the host's world, its character type is set to "live
-- player", so the scripts' IsWhiteGhost() branches (soul-form HP, the walk home after a boss) no longer apply to it.
COOP_InGameStart = InGameStart;
function InGameStart(proxy, param)
	if proxy:IsClient() == true and proxy:IsWhiteGhost() == true then
		print("[coop] guest: white phantom -> live player");
		proxy:SetChrType(10000, CHR_TYPE_LivePlayer);
	end
	COOP_InGameStart(proxy, param);
	print("[coop] InGameStart: client " .. tostring(proxy:IsClient()) .. " host " .. tostring(proxy:IsHost()) ..
	      " live " .. tostring(proxy:IsLivePlayer()) .. " white " .. tostring(proxy:IsWhiteGhost()) ..
	      " party " .. tostring(proxy:IsInParty()));
end

-- Step B: a boss dies and everybody stays. The game's BlockClear2 (event 4050, sent to every player when a boss
-- dies) ends five seconds later in BlockClear2_1: with friends in the party it opens the "rate your helpers" menu,
-- then leaves the session and walks the phantoms home. Here it does what a lone player's version does -- revive a
-- soul-form player, take the real clear bonus (souls and the demon's soul, GetSoloClearBonus), let death and
-- reloads happen again -- and nobody leaves.
function BlockClear2_1(proxy, param)
	print("[coop] BlockClear2_1: boss " .. tostring(ClearBossId) .. " dead, staying together");
	if proxy:IsGreyGhost() == true then
		if proxy:IsAlive(10000) == true then
			proxy:RevivePlayer();
			proxy:SetReviveWait( false );
		end
		proxy:SetEventFlag( 15112,true );
		proxy:SetEventFlag( 15113,true );
		proxy:SetEventFlag( 15114,true );
		proxy:SetEventFlag( 15115,true );
		proxy:SetEventFlag( 15116,true );
		proxy:SetEventFlag( 15117,true );
		proxy:SetEventFlag( 15118,true );
		proxy:SetEventFlag( 15119,true );
		proxy:EraseEventSpecialEffect( 10000, 101 );
	end
	proxy:NotNetMessage_begin();
		proxy:OnKeyTime2( 4050, "COOP_BlockClearDone", 3.0, 0, 6, once );
	proxy:NotNetMessage_end();
end

function COOP_BlockClearDone(proxy, param)
	proxy:SetSubMenuBrake( false );
	proxy:GetSoloClearBonus(ClearBossId);
	proxy:SetEventFlag(4047, false);
	proxy:SetEventFlag( 4000, false );
	if proxy:IsAlive(10000) == true then
		proxy:SetEventFlag( 4067, false );
	end
	print("[coop] boss " .. tostring(ClearBossId) .. ": bonus taken, nobody left; client " .. tostring(proxy:IsClient()) ..
	      " party " .. tostring(proxy:IsInParty()) .. " live " .. tostring(proxy:IsLivePlayer()));
end

-- DEV TEST (tools/make_coop.py --test-kill <character id>:<seconds>): the host kills that character that many
-- seconds after a guest is in its party, so a boss's death can be tested without fighting it. COOP_TEST_KILL is
-- set by a line make_coop.py puts after this module; it is nil in a normal build.
function COOP_TestKillCheck(proxy, param)
	return proxy:IsInParty() == true;
end
function COOP_TestKillArm(proxy, param)
	print("[coop] test: a guest is here; character " .. tostring(COOP_TEST_KILL) .. " dies in " .. tostring(COOP_TEST_AFTER) .. " s");
	proxy:NotNetMessage_begin();
		proxy:OnKeyTime2( 9971, "COOP_TestKill", COOP_TEST_AFTER, 0, 0, once );
	proxy:NotNetMessage_end();
end
function COOP_TestKill(proxy, param)
	print("[coop] test: ForceDead " .. tostring(COOP_TEST_KILL));
	proxy:ForceDead(COOP_TEST_KILL);
end
COOP_InGameStart_B = InGameStart;
function InGameStart(proxy, param)
	COOP_InGameStart_B(proxy, param);
	if COOP_TEST_KILL ~= nil and proxy:IsClient() == false then
		proxy:NotNetMessage_begin();
			proxy:OnRegistFunc( 9970, "COOP_TestKillCheck", "COOP_TestKillArm", 30, once );
		proxy:NotNetMessage_end();
	end
end
