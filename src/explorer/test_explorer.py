from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from unittest.mock import patch, Mock
from recommender.models import SavedPlaylist
from explorer.models import Playlist, Song  # Still needed for SpotifyAPIHelper tests
from explorer.views import SpotifyAPIHelper


class SavedPlaylistModelTests(TestCase):
    """Tests for SavedPlaylist model"""

    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='testpass')

    def test_create_saved_playlist(self):
        """Test creating a saved playlist with all fields"""
        playlist = SavedPlaylist.objects.create(
            playlist_name='Test Playlist',
            playlist_id='sp123',
            description='Sample playlist',
            creator_user_id='user123',
            creator_display_name='tester',
            like_count=5,
            spotify_uri='spotify:playlist:sp123'
        )
        self.assertEqual(str(playlist), 'Test Playlist (tester)')
        self.assertEqual(playlist.like_count, 5)
        self.assertEqual(playlist.creator_display_name, 'tester')
        self.assertEqual(playlist.playlist_id, 'sp123')

    def test_saved_playlist_ordering(self):
        """Test that saved playlists are ordered by created_at descending"""
        p1 = SavedPlaylist.objects.create(
            playlist_name='First',
            playlist_id='first',
            creator_user_id='user1',
            creator_display_name='tester',
            like_count=1
        )
        p2 = SavedPlaylist.objects.create(
            playlist_name='Second',
            playlist_id='second',
            creator_user_id='user1',
            creator_display_name='tester',
            like_count=10
        )
        # Default ordering is by -created_at, so p2 should be first
        playlists = SavedPlaylist.objects.all()
        self.assertEqual(playlists[0], p2)
        self.assertEqual(playlists[1], p1)


class SearchViewTests(TestCase):
    """Tests for the search functionality"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='bob', password='123')
        self.p1 = SavedPlaylist.objects.create(
            playlist_name='Rock Mix',
            playlist_id='rockmix',
            description='Best rock songs',
            creator_user_id='bob_id',
            creator_display_name='bob',
            like_count=4
        )

    def test_search_view_renders(self):
        """Test that search view renders successfully"""
        response = self.client.get(reverse('search'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'explorer/search.html')

    def test_search_by_name(self):
        """Test searching for playlists by name"""
        response = self.client.get(reverse('search') + '?q=rock')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rock Mix')

    def test_search_by_description(self):
        """Test searching for playlists by description"""
        response = self.client.get(reverse('search') + '?q=Best')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rock Mix')

    def test_search_by_song_name(self):
        """Test searching for playlists by song name - not supported with SavedPlaylist"""
        # SavedPlaylist doesn't have song relations, so searching by song name won't work
        # This test now verifies that searching for a song name returns no results
        response = self.client.get(reverse('search') + '?q=Thunderstruck')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['results_count'], 0)

    def test_search_by_creator_username(self):
        """Test searching for playlists by creator username"""
        response = self.client.get(reverse('search') + '?q=bob')
        self.assertContains(response, 'Rock Mix')

    def test_search_case_insensitive(self):
        """Test that search is case insensitive"""
        response = self.client.get(reverse('search') + '?q=ROCK')
        self.assertContains(response, 'Rock Mix')

    def test_search_no_results_shows_empty_message(self):
        """Test that search shows appropriate message when no results found"""
        response = self.client.get(reverse('search') + '?q=Nonexistent')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No playlists found')

    def test_search_fetches_from_spotify_when_not_found_locally(self):
        """Test that search returns empty when no local results found"""
        # Since we removed Spotify fallback, searching for nonexistent playlists returns empty
        SavedPlaylist.objects.all().delete()
        response = self.client.get(reverse('search') + '?q=something')
        self.assertEqual(response.status_code, 200)
        # Should return empty results
        self.assertEqual(response.context['results_count'], 0)

    def test_search_without_query(self):
        """Test search without query parameter returns all playlists"""
        response = self.client.get(reverse('search'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rock Mix')

    def test_search_results_count_in_context(self):
        """Test that search results count is in context"""
        response = self.client.get(reverse('search') + '?q=rock')
        self.assertIn('results_count', response.context)
        self.assertEqual(response.context['results_count'], 1)

    def test_search_displays_playlist_card_structure(self):
        """Test that search results use the new playlist card structure"""
        response = self.client.get(reverse('search') + '?q=rock')
        self.assertEqual(response.status_code, 200)
        # Check for new playlist card structure elements
        self.assertContains(response, 'playlist-card')
        self.assertContains(response, 'playlist-image')
        self.assertContains(response, 'playlist-info-compact')


class ProfileViewTests(TestCase):
    """Tests for user profile view"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='charlie', password='test')
        self.spotify_user_id = 'charlie_spotify_id'
        SavedPlaylist.objects.create(
            playlist_name='User Playlist 1',
            playlist_id='user1',
            creator_user_id=self.spotify_user_id,
            creator_display_name='charlie'
        )
        SavedPlaylist.objects.create(
            playlist_name='User Playlist 2',
            playlist_id='user2',
            creator_user_id=self.spotify_user_id,
            creator_display_name='charlie'
        )

    def test_profile_page_valid_user(self):
        """Test that profile page displays for valid user"""
        response = self.client.get(reverse('profile', args=[self.spotify_user_id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'explorer/profile.html')
        self.assertContains(response, "charlie's Playlists")

    def test_profile_shows_user_playlists(self):
        """Test that profile shows all user's playlists"""
        response = self.client.get(reverse('profile', args=[self.spotify_user_id]))
        self.assertContains(response, 'User Playlist 1')
        self.assertContains(response, 'User Playlist 2')

    def test_profile_page_invalid_user(self):
        """Test that profile page returns 404 for non-existent user"""
        response = self.client.get(reverse('profile', args=['nonexistent_user_id']))
        self.assertEqual(response.status_code, 404)

    def test_profile_shows_only_user_playlists(self):
        """Test that profile only shows playlists created by that user"""
        other_user_id = 'other_spotify_id'
        SavedPlaylist.objects.create(
            playlist_name='Other Playlist',
            playlist_id='other',
            creator_user_id=other_user_id,
            creator_display_name='other'
        )

        response = self.client.get(reverse('profile', args=[self.spotify_user_id]))
        self.assertContains(response, 'User Playlist 1')
        self.assertNotContains(response, 'Other Playlist')

    def test_profile_displays_playlist_card_structure(self):
        """Test that profile page uses the new playlist card structure"""
        response = self.client.get(reverse('profile', args=[self.spotify_user_id]))
        self.assertEqual(response.status_code, 200)
        # Check for new playlist card structure elements
        self.assertContains(response, 'playlist-card')
        self.assertContains(response, 'playlist-image')
        self.assertContains(response, 'playlist-info-compact')


class LogoutViewTests(TestCase):
    """Tests for logout functionality"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='pass')

    def test_logout_clears_session(self):
        """Test that logout clears all session data"""
        session = self.client.session
        session['key'] = 'value'
        session['spotify_token'] = 'token123'
        session.save()

        response = self.client.get(reverse('logout'))

        self.assertNotIn('key', self.client.session)
        self.assertNotIn('spotify_token', self.client.session)
        self.assertEqual(response.status_code, 302)

    def test_logout_redirects_to_home(self):
        """Test that logout redirects to home page"""
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('home'))


class SpotifyAPIHelperTests(TestCase):
    """Tests for Spotify API helper functions"""

    @patch('explorer.views.requests.post')
    def test_get_access_token_success(self, mock_post):
        """Test successful token retrieval"""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'access_token': 'abc123'}

        token = SpotifyAPIHelper.get_access_token()

        self.assertEqual(token, 'abc123')
        mock_post.assert_called_once()

    @patch('explorer.views.requests.post')
    def test_get_access_token_failure(self, mock_post):
        """Test token retrieval failure raises exception"""
        mock_post.return_value.status_code = 400

        with self.assertRaises(Exception) as context:
            SpotifyAPIHelper.get_access_token()

        self.assertIn('Failed to get Spotify access token', str(context.exception))

    @patch('explorer.views.requests.get')
    @patch('explorer.views.SpotifyAPIHelper.get_access_token', return_value='tok')
    def test_fetch_playlists_success(self, mock_token, mock_get):
        """Test successful playlist fetching from Spotify"""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            'playlists': {'items': [
                {'id': 'p1', 'name': 'Playlist 1'},
                {'id': 'p2', 'name': 'Playlist 2'}
            ]}
        }

        data = SpotifyAPIHelper.fetch_playlists('query')

        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['id'], 'p1')
        self.assertEqual(data[1]['id'], 'p2')

    @patch('explorer.views.requests.get')
    @patch('explorer.views.SpotifyAPIHelper.get_access_token', return_value='tok')
    def test_fetch_playlists_error(self, mock_token, mock_get):
        """Test playlist fetching handles errors gracefully"""
        mock_get.return_value.status_code = 400

        data = SpotifyAPIHelper.fetch_playlists('query')

        self.assertEqual(data, [])

    @patch('explorer.views.requests.get')
    @patch('explorer.views.SpotifyAPIHelper.get_access_token', return_value='tok')
    def test_fetch_playlists_with_limit(self, mock_token, mock_get):
        """Test fetch playlists respects limit parameter"""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            'playlists': {'items': [{'id': f'p{i}', 'name': f'Playlist {i}'} for i in range(5)]}
        }

        SpotifyAPIHelper.fetch_playlists('query', limit=5)

        # Check that the API was called with the correct limit
        call_args = mock_get.call_args
        self.assertEqual(call_args[1]['params']['limit'], 5)

    @patch('explorer.views.User.objects.get_or_create')
    @patch('explorer.views.Playlist.objects.get_or_create')
    def test_import_playlist(self, mock_playlist_create, mock_user_create):
        """Test importing a playlist from Spotify data"""
        mock_user = Mock()
        mock_user.username = 'spotify_user'
        mock_user_create.return_value = (mock_user, True)

        mock_playlist = Mock()
        mock_playlist.name = 'Imported Playlist'
        mock_playlist_create.return_value = (mock_playlist, True)

        playlist_data = {
            'id': 'sp123',
            'name': 'Imported Playlist',
            'description': 'A test playlist',
            'images': [{'url': 'http://image.url'}],
            'followers': {'total': 100},
            'uri': 'spotify:playlist:sp123',
            'tracks': {'href': 'http://tracks'}
        }

        result = SpotifyAPIHelper.import_playlist(playlist_data)

        self.assertIsNotNone(result)
        mock_playlist_create.assert_called_once()

    @patch('explorer.views.requests.get')
    @patch('explorer.views.SpotifyAPIHelper.get_access_token', return_value='token')
    def test_fetch_and_add_songs(self, mock_token, mock_get):
        """Test fetching and adding songs to a playlist"""
        user = User.objects.create_user(username='test', password='pass')
        playlist = Playlist.objects.create(
            name='Test',
            creator=user,
            spotify_id='test123'
        )

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            'items': [
                {
                    'track': {
                        'id': 't1',
                        'name': 'Song 1',
                        'artists': [{'name': 'Artist 1'}]
                    }
                },
                {
                    'track': {
                        'id': 't2',
                        'name': 'Song 2',
                        'artists': [{'name': 'Artist 2'}, {'name': 'Artist 3'}]
                    }
                }
            ]
        }

        SpotifyAPIHelper.fetch_and_add_songs(playlist, 'http://tracks', limit=2)

        self.assertEqual(playlist.sample_songs.count(), 2)
        songs = playlist.sample_songs.all()
        self.assertEqual(songs[0].name, 'Song 1')
        self.assertEqual(songs[1].artist, 'Artist 2, Artist 3')


class PlaylistCardTemplateTests(TestCase):
    """Tests for the new playlist card template structure"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.playlist = SavedPlaylist.objects.create(
            playlist_name='Test Playlist',
            playlist_id='test123',
            description='Test Description',
            creator_user_id='user_test123',
            creator_display_name='testuser',
            like_count=10,
            spotify_uri='spotify:playlist:test123',
            cover_image='http://example.com/image.jpg'
        )

    def test_playlist_card_has_correct_structure(self):
        """Test that playlist card has the new structure with all elements"""
        response = self.client.get(reverse('search') + '?q=Test')
        self.assertEqual(response.status_code, 200)

        # Check for main structure elements
        self.assertContains(response, 'class="playlist-card"')
        self.assertContains(response, 'class="playlist-image"')
        self.assertContains(response, 'class="playlist-info-compact"')
        self.assertContains(response, 'class="playlist-title"')

    def test_playlist_card_displays_cover_image(self):
        """Test that playlist card displays cover image when available"""
        response = self.client.get(reverse('search') + '?q=Test')
        self.assertContains(response, 'http://example.com/image.jpg')
        self.assertContains(response, 'alt="Test Playlist"')

    def test_playlist_card_displays_placeholder_when_no_image(self):
        """Test that playlist card displays emoji placeholder when no cover image"""
        self.playlist.cover_image = ''
        self.playlist.save()

        response = self.client.get(reverse('search') + '?q=Test')
        # The template shows a music emoji 🎵 when there's no image
        self.assertContains(response, '🎵')

    def test_playlist_card_displays_creator_username(self):
        """Test that playlist card displays creator username"""
        response = self.client.get(reverse('search') + '?q=Test')
        self.assertContains(response, 'testuser')
        self.assertContains(response, 'creator-name')

    def test_playlist_card_displays_likes(self):
        """Test that playlist card displays likes count"""
        response = self.client.get(reverse('search') + '?q=Test')
        self.assertContains(response, 'likes-count')
        self.assertContains(response, '10')
        self.assertContains(response, '👍')

    def test_playlist_card_shows_spotify_link_when_available(self):
        """Test that playlist card shows Spotify link when spotify_uri is set"""
        response = self.client.get(reverse('search') + '?q=Test')
        self.assertContains(response, 'View on Spotify')
        self.assertContains(response, f'https://open.spotify.com/playlist/{self.playlist.playlist_id}')

    def test_playlist_card_hides_spotify_link_when_not_available(self):
        """Test that playlist card hides Spotify link when spotify_uri is not set"""
        self.playlist.spotify_uri = ''
        self.playlist.save()

        response = self.client.get(reverse('search') + '?q=Test')
        self.assertNotContains(response, 'View on Spotify')
