# src/dashboard/models.py
"""Models for tracking user follow relationships"""
from django.db import models


class UserFollow(models.Model):
    """Tracks user follow relationships"""

    follower_user_id = models.CharField(max_length=64, db_index=True)
    follower_display_name = models.CharField(max_length=255)
    following_user_id = models.CharField(max_length=64, db_index=True)
    following_display_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('follower_user_id', 'following_user_id')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['follower_user_id', '-created_at']),
            models.Index(fields=['following_user_id', '-created_at']),
        ]

    def __str__(self):
        return f"{self.follower_display_name} follows {self.following_display_name}"
