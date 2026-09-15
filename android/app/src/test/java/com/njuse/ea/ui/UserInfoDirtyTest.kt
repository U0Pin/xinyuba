package com.njuse.ea.ui

import com.njuse.ea.ui.viewmodel.UserInfo
import com.njuse.ea.ui.viewmodel.draftIsDirty
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 用户信息 dirty 判定：null draft = 无改动；draft 与 baseline 相同不算 dirty */
class UserInfoDirtyTest {

    private val baseline = UserInfo(nickname = "猫", gender = "female", age = 20, tonePreference = "gentle")

    @Test
    fun `null draft is not dirty`() {
        assertFalse(draftIsDirty(null, baseline))
    }

    @Test
    fun `draft equal to baseline is not dirty`() {
        assertFalse(draftIsDirty(baseline, baseline))
    }

    @Test
    fun `draft differing in any field is dirty`() {
        assertTrue(draftIsDirty(baseline.copy(nickname = "改"), baseline))
        assertTrue(draftIsDirty(baseline.copy(gender = "male"), baseline))
        assertTrue(draftIsDirty(baseline.copy(age = 21), baseline))
        assertTrue(draftIsDirty(baseline.copy(tonePreference = "lively"), baseline))
    }
}
