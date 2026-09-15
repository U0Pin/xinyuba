package com.njuse.ea.data

import com.njuse.ea.ui.viewmodel.UserInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** UserInfo → PUT user-info 请求体的空值语义（""/0 → null，即"未设置"） */
class UserInfoRequestTest {

    @Test
    fun `full user info maps all four fields`() {
        val req = UserInfo(nickname = "小猫", gender = "female", age = 24, tonePreference = "gentle").toRequest()
        assertEquals("小猫", req.nickname)
        assertEquals("female", req.gender)
        assertEquals(24, req.age)
        assertEquals("gentle", req.tone_preference)
    }

    @Test
    fun `blank strings and zero age map to null`() {
        val req = UserInfo(nickname = "", gender = "", age = 0, tonePreference = "").toRequest()
        assertNull(req.nickname)
        assertNull(req.gender)
        assertNull(req.age)
        assertNull(req.tone_preference)
    }

    @Test
    fun `empty UserInfo equals reset request body (all null)`() {
        val req = UserInfo().toRequest()
        assertEquals(UserInfoRequest(), req)
    }

    @Test
    fun `blank nickname is null but whitespace-only nickname also treated as unset`() {
        assertNull(UserInfo(nickname = "   ").toRequest().nickname)
    }
}
