# serializers.py
import os
from rest_framework import serializers
from django.db import models
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import CustomUser, Friendship
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

    def validate_password(self, value):
        candidate = CustomUser(
            email=self.initial_data.get('email', ''),
            username=self.initial_data.get('username', '')
        )
        try:
            validate_password(value, candidate)
        except ValidationError as exc:
            raise serializers.ValidationError(exc.messages)
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


class FriendshipSerializer(serializers.ModelSerializer):
    from_user = serializers.SerializerMethodField()
    to_user = serializers.SerializerMethodField()
    
    class Meta:
        model = Friendship
        fields = ['id', 'from_user', 'to_user', 'status', 'created_at']
        read_only_fields = ['id', 'from_user', 'to_user', 'status', 'created_at']
    
    def get_from_user(self, obj):
        return {
            'id': obj.from_user.id,
            'username': obj.from_user.username,
            'email': obj.from_user.email,
            'first_name': obj.from_user.first_name,
            'last_name': obj.from_user.last_name,
            'profile_picture': obj.from_user.profile_picture,
        }
    
    def get_to_user(self, obj):
        return {
            'id': obj.to_user.id,
            'username': obj.to_user.username,
            'email': obj.to_user.email,
            'first_name': obj.to_user.first_name,
            'last_name': obj.to_user.last_name,
            'profile_picture': obj.to_user.profile_picture,
        }


class FriendRequestSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    
    def validate_username(self, value):
        request_user = self.context['request'].user
        if value.casefold() == request_user.username.casefold():
            raise serializers.ValidationError("You cannot send a friend request to yourself.")
        
        try:
            target_user = CustomUser.objects.get(username__iexact=value, is_active=True)
        except CustomUser.DoesNotExist:
            raise serializers.ValidationError("User not found.")
        
        # Check if friendship already exists
        existing = Friendship.objects.filter(
            models.Q(from_user=request_user, to_user=target_user) |
            models.Q(from_user=target_user, to_user=request_user)
        ).first()
        
        if existing:
            if existing.status == Friendship.Status.ACCEPTED:
                raise serializers.ValidationError("You are already friends with this user.")
            elif existing.status == Friendship.Status.PENDING:
                if existing.from_user == request_user:
                    raise serializers.ValidationError("Friend request already sent.")
                else:
                    raise serializers.ValidationError("This user has already sent you a friend request.")
        
        return value


class UserSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'profile_picture']


class FriendSerializer(serializers.ModelSerializer):
    """Serializer for displaying friends with balance info"""
    balance = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'profile_picture', 'balance', 'status']
    
    def get_balance(self, obj):
        # This will be calculated in the view
        return self.context.get('balances', {}).get(obj.id, 0)
    
    def get_status(self, obj):
        balance = self.context.get('balances', {}).get(obj.id, 0)
        if balance > 0:
            return 'owed'
        elif balance < 0:
            return 'owe'
        return 'settled'