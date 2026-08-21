# serializers.py
import os
from rest_framework import serializers
from .models import CustomUser
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

class RegisterSerializer(serializers.ModelSerializer):
    firstName = serializers.CharField(source='first_name', max_length=150)
    lastName = serializers.CharField(source='last_name', max_length=150)
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model = CustomUser
        fields = ('email', 'firstName', 'lastName', 'password', 'username')
        # 🌟 FORCE DRF implicit unique validators to use your clean string
        extra_kwargs = {
            'email': {
                'error_messages': {
                    'unique': 'User already exists.'
                }
            },
            'username': {
                'error_messages': {
                    'unique': 'User already exists.'
                }
            }
        }

    def validate_email(self, value):
        if CustomUser.objects.filter(email=value).exists():
            raise serializers.ValidationError("User already exists.")
        return value

    def validate_username(self, value):
        if CustomUser.objects.filter(username=value).exists():
            raise serializers.ValidationError("Username already exists.")
        return value

    def create(self, validated_data):
        user = CustomUser.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            is_active=False  # Require email verification
        )
        return user

class UserProfileSerializer(serializers.ModelSerializer):
    firstName = serializers.CharField(
        source='first_name', 
        max_length=150, 
        allow_blank=True, 
        required=False
    )
    lastName = serializers.CharField(
        source='last_name', 
        max_length=150, 
        allow_blank=True, 
        required=False
    )
    profilePicture = serializers.CharField(
        source='profile_picture', 
        allow_blank=True, 
        required=False,
        read_only=True  # Client reads this string URL, but sends files via pictureFile
    )
    # 📸 Accept multipart file uploads from Next.js (write-only)
    pictureFile = serializers.ImageField(write_only=True, required=False)

    class Meta:
        model = CustomUser
        fields = (
            'id', 
            'email', 
            'username', 
            'firstName', 
            'lastName', 
            'profilePicture', 
            'pictureFile', 
            'is_active'
        )
        read_only_fields = ('id', 'email', 'is_active', 'profilePicture')

    # 🔒 UNIQUE USERNAME VALIDATOR
    def validate_username(self, value):
        user = self.instance  # The user currently performing the update

        # Check if another user already owns this username (excluding current user)
        existing_user = CustomUser.objects.filter(username__iexact=value)
        if user:
            existing_user = existing_user.exclude(pk=user.pk)

        if existing_user.exists():
            raise serializers.ValidationError("A user with that username already exists.")

        return value

    def update(self, instance, validated_data):
        # 1. Handle image file upload if present
        picture_file = validated_data.pop('pictureFile', None)

        if picture_file:
            # Delete old image if it's a local file (not an external Google URL)
            if instance.profile_picture and not instance.profile_picture.startswith(('http://', 'https://')):
                if default_storage.exists(instance.profile_picture):
                    default_storage.delete(instance.profile_picture)

            # Save new file with unique path
            ext = os.path.splitext(picture_file.name)[1]
            file_path = f"profile_pics/user_{instance.id}{ext}"
            saved_path = default_storage.save(file_path, ContentFile(picture_file.read()))
            
            instance.profile_picture = saved_path

        # 2. Update remaining fields (first_name, last_name, username, etc.)
        return super().update(instance, validated_data)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        raw_picture = instance.profile_picture
        if raw_picture:
            # 1. Google OAuth or external URLs
            if raw_picture.startswith(('http://', 'https://')):
                data['profilePicture'] = raw_picture
            # 2. Local uploaded files
            elif request:
                # Ensure path starts with leading slash for build_absolute_uri
                url_path = default_storage.url(raw_picture)
                data['profilePicture'] = request.build_absolute_uri(url_path)

        return data

class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(min_length=6)