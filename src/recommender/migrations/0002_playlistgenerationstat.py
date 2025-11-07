from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recommender", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlaylistGenerationStat",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "user_identifier",
                    models.CharField(db_index=True, max_length=128),
                ),
                ("prompt", models.TextField()),
                ("track_count", models.PositiveIntegerField(default=0)),
                ("total_duration_ms", models.BigIntegerField(default=0)),
                ("top_genre", models.CharField(blank=True, max_length=128)),
                ("avg_novelty", models.FloatField(blank=True, null=True)),
                ("stats", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="playlistgenerationstat",
            index=models.Index(
                fields=("user_identifier", "-created_at"),
                name="recommende_user_id_9fb003_idx",
            ),
        ),
    ]
